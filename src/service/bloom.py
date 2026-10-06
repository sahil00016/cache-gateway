"""Bloom filter for cache penetration protection.

A Bloom filter answers "definitely not present" or "might be present", trading
perfect recall for space efficiency. Used here to reject requests for IDs that
do not exist in Postgres before they reach Redis or the database.

**The deletion problem.** A Bloom filter cannot remove keys. When a product is
soft-deleted, the filter still reports "might be present", so the request falls
through to Postgres and correctly 404s. The filter degrades to a false positive,
which is safe but erodes protection over time. The mitigation: periodic rebuild
via the admin endpoint, with the rebuild cost measured and documented.

**Sizing.** The filter is dimensioned from ``bloom_expected_items`` (n) and
``bloom_fp_rate`` (p). The arithmetic:

  m = -(n * ln(p)) / (ln(2)^2)  ≈ 9.585 bits/item at p=0.01
  k = (m/n) * ln(2)             ≈ 7 hashes at p=0.01

For n=1M and p=0.01, m ≈ 9.585 Mbit ≈ 1.2 MB per filter. With N workers there
are N in-process copies, ~4.8 MB total at the default worker count.

**Per-worker.** The filter lives in process memory, not Redis. With 4 uvicorn
workers there are 4 independent filters. A key added by one worker is invisible
to the others until they rebuild, so penetration protection is eventually
consistent across the fleet, not immediate. This is acceptable because the
consequence of a filter miss is a DB query, not a crash.
"""

import logging
import tempfile
import time
from pathlib import Path

from pybloomfilter import BloomFilter  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import (
    bloom_adds_total,
    bloom_build_duration_seconds,
    bloom_measured_fp_rate,
    bloom_queries_total,
    bloom_saturation,
)
from src.model.product import Product

logger = logging.getLogger(__name__)


class BloomFilterService:
    """In-process Bloom filter for product ID existence checks.

    This is a per-worker singleton. Each uvicorn worker builds its own copy at
    startup. Keys added or removed in one worker do not affect others until the
    next rebuild.
    """

    def __init__(
        self,
        *,
        expected_items: int,
        fp_rate: float,
        enabled: bool = True,
    ) -> None:
        """Initialize an empty filter with the given capacity.

        Does not populate the filter -- call :meth:`build_from_db` separately,
        typically during app startup.

        Args:
            expected_items: The n in the sizing calculation. If more items are
                added, the false positive rate degrades.
            fp_rate: Target false positive rate, between 0 and 1.
            enabled: Whether penetration protection is active. When disabled,
                :meth:`__contains__` always returns True (no filtering).
        """
        self._expected_items = expected_items
        self._fp_rate = fp_rate
        self._enabled = enabled
        self._items_added = 0
        self._false_positives = 0  # Tracked opportunistically for measured FP rate
        self._total_lookups = 0

        # pybloomfiltermmap3 requires a file path. Use a temp file that lives
        # for the process lifetime.
        self._temp_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
            prefix="bloom_",
            suffix=".bloom",
            delete=False,
        )
        self._filter_path = Path(self._temp_file.name)

        # Create the filter. The library calculates m and k from capacity and
        # error_rate internally.
        self._filter = BloomFilter(
            capacity=expected_items,
            error_rate=fp_rate,
            filename=str(self._filter_path),
        )

        # Log the calculated dimensions so they appear in startup logs
        m_bits = self._filter.num_bits
        k_hashes = self._filter.num_hashes
        m_mb = m_bits / (8 * 1024 * 1024)

        logger.info(
            "Bloom filter initialized",
            extra={
                "expected_items": expected_items,
                "target_fp_rate": fp_rate,
                "m_bits": m_bits,
                "m_mb": round(m_mb, 2),
                "k_hashes": k_hashes,
                "enabled": enabled,
                "filter_path": str(self._filter_path),
            },
        )

    def __contains__(self, product_id: int) -> bool:
        """Check if a product ID might exist in Postgres.

        Args:
            product_id: The ID to check.

        Returns:
            True if the ID might exist (or if the filter is disabled), False if
            it definitely does not exist.
        """
        if not self._active():
            return True

        self._total_lookups += 1
        key = self._make_key(product_id)
        result = key in self._filter

        bloom_queries_total.labels(result="hit" if result else "miss").inc()

        # Update measured FP rate metric
        if self._total_lookups > 0:
            measured_fp = self._false_positives / self._total_lookups
            bloom_measured_fp_rate.set(measured_fp)

        return result

    def add(self, product_id: int) -> None:
        """Add a product ID to the filter.

        Idempotent -- adding the same key twice has no effect on correctness,
        though it does increment the saturation counter.

        Args:
            product_id: The ID to add.
        """
        if not self._enabled:
            return

        key = self._make_key(product_id)
        self._filter.add(key)
        self._items_added += 1
        bloom_adds_total.inc()

        # Update saturation metric
        saturation = self._items_added / self._expected_items
        bloom_saturation.set(saturation)

        if saturation > 1.0 and self._items_added % 10000 == 0:
            logger.warning(
                "Bloom filter saturation exceeds capacity",
                extra={
                    "items_added": self._items_added,
                    "expected_items": self._expected_items,
                    "saturation": round(saturation, 3),
                    "consequence": "false positive rate is degrading beyond target",
                },
            )

    def mark_false_positive(self) -> None:
        """Record that the last lookup was a false positive.

        Call this when a Bloom "hit" turns out to be a miss in Postgres. Used
        to track the measured FP rate vs. the predicted rate.
        """
        if not self._enabled:
            return
        self._false_positives += 1

    async def build_from_db(self, session: AsyncSession) -> dict[str, int | float]:
        """Populate the filter from all live product IDs in Postgres.

        Replaces the filter's contents -- any previously added keys are lost.
        This is the recovery path for the deletion problem: when the filter has
        accumulated too many deleted-but-still-present keys, rebuild it from the
        current truth.

        Args:
            session: Database session. Should be a dedicated session, not shared
                with request handling.

        Returns:
            A dict with rebuild stats: count, duration_seconds.
        """
        start = time.perf_counter()

        logger.info("Rebuilding Bloom filter from Postgres")

        # Recreate the filter to clear old state
        self._filter.close()
        self._filter_path.unlink(missing_ok=True)
        self._filter = BloomFilter(
            capacity=self._expected_items,
            error_rate=self._fp_rate,
            filename=str(self._filter_path),
        )
        self._items_added = 0
        self._false_positives = 0
        self._total_lookups = 0

        # Fetch all live product IDs in one query
        statement = select(Product.id).where(Product.deleted_at.is_(None))
        result = await session.execute(statement)
        product_ids = [row[0] for row in result.all()]

        # Add each ID to the filter
        for product_id in product_ids:
            self.add(product_id)

        duration = time.perf_counter() - start
        bloom_build_duration_seconds.observe(duration)

        logger.info(
            "Bloom filter rebuild complete",
            extra={
                "count": len(product_ids),
                "duration_seconds": round(duration, 3),
                "saturation": round(len(product_ids) / self._expected_items, 3),
            },
        )

        return {
            "count": len(product_ids),
            "duration_seconds": round(duration, 3),
        }

    def _active(self) -> bool:
        """Return whether lookups should consult the filter right now.

        Distinct from ``enabled``: that records whether the filter was *built*
        at startup, and a filter that was never built cannot answer. This also
        honours the runtime overlay, so the penetration demo can switch
        protection off and on without a restart -- and critically, without
        rebuilding from a million rows on the way back.

        Returns:
            True when the filter is both built and switched on.
        """
        if not self._enabled:
            return False

        from src.core.runtime_flags import get_runtime_flags  # noqa: PLC0415 -- import cycle

        return get_runtime_flags().bloom_enabled

    @property
    def enabled(self) -> bool:
        """Return whether the filter is enabled.

        Returns:
            True if penetration protection is active.
        """
        return self._enabled

    def get_stats(self) -> dict[str, int | float]:
        """Return current filter statistics.

        Returns:
            A dict with capacity, items_added, saturation, target_fp_rate,
            measured_fp_rate, total_lookups, false_positives.
        """
        saturation = self._items_added / self._expected_items if self._expected_items > 0 else 0
        measured_fp = (
            self._false_positives / self._total_lookups if self._total_lookups > 0 else 0.0
        )

        return {
            "enabled": self._enabled,
            "expected_items": self._expected_items,
            "items_added": self._items_added,
            "saturation": round(saturation, 4),
            "target_fp_rate": self._fp_rate,
            "measured_fp_rate": round(measured_fp, 4),
            "total_lookups": self._total_lookups,
            "false_positives": self._false_positives,
            "m_bits": self._filter.num_bits,
            "k_hashes": self._filter.num_hashes,
        }

    def close(self) -> None:
        """Clean up the temporary filter file.

        Call this during application shutdown.
        """
        try:
            self._filter.close()
            self._filter_path.unlink(missing_ok=True)
            logger.info("Bloom filter closed and temp file removed")
        except OSError as exc:
            logger.warning(f"Failed to clean up Bloom filter: {exc}")

    @staticmethod
    def _make_key(product_id: int) -> str:
        """Convert a product ID to a filter key.

        Returns a string because pybloomfiltermmap3 hashes strings, not ints.

        Args:
            product_id: The product ID.

        Returns:
            The key to use in the filter.
        """
        return f"product:{product_id}"


# Global singleton instance, initialized during app startup
_bloom_filter: BloomFilterService | None = None


def init_bloom_filter(
    *,
    expected_items: int,
    fp_rate: float,
    enabled: bool,
) -> BloomFilterService:
    """Initialize the process-wide Bloom filter singleton.

    Call this once during app startup, before any requests are handled.

    Args:
        expected_items: Filter capacity (n in the sizing formula).
        fp_rate: Target false positive rate (p in the sizing formula).
        enabled: Whether to actually filter queries.

    Returns:
        The initialized filter instance.
    """
    global _bloom_filter  # noqa: PLW0603
    _bloom_filter = BloomFilterService(
        expected_items=expected_items,
        fp_rate=fp_rate,
        enabled=enabled,
    )
    return _bloom_filter


def get_bloom_filter() -> BloomFilterService:
    """Return the process-wide Bloom filter singleton.

    Raises:
        RuntimeError: If the filter has not been initialized yet.

    Returns:
        The filter instance.
    """
    if _bloom_filter is None:
        raise RuntimeError("Bloom filter not initialized. Call init_bloom_filter during startup.")
    return _bloom_filter


__all__ = [
    "BloomFilterService",
    "get_bloom_filter",
    "init_bloom_filter",
]
