"""Seed the products table with a reproducible dataset.

Benchmarks are only comparable if the data underneath them is identical, so
this script is deterministic: the same ``--count`` and ``--seed`` always
produce byte-identical rows. Re-running it truncates first rather than
appending, because a benchmark against 1.5M rows is not comparable with one
against 1M.

Rows are written with PostgreSQL ``COPY`` rather than INSERT. At a million
rows the difference is minutes versus seconds, and a seed slow enough to be
annoying is a seed that gets skipped -- which is how benchmarks end up running
against whatever happened to be in the database.

    python -m scripts.seed_products --count 1000000
"""

import argparse
import asyncio
import io
import random
import sys
import time

from sqlalchemy import text

from src.common.settings import get_settings
from src.db.engine import dispose_engine, init_engine

CATEGORIES = (
    "electronics",
    "books",
    "clothing",
    "home",
    "garden",
    "toys",
    "sports",
    "grocery",
    "beauty",
    "automotive",
)
ADJECTIVES = ("compact", "premium", "rugged", "classic", "wireless", "portable")
NOUNS = ("widget", "adapter", "mount", "case", "cable", "stand", "kit")

_COPY_CHUNK_ROWS = 50_000


def _row(index: int, rng: random.Random) -> str:
    """Build one tab-separated COPY line.

    Args:
        index: 1-based row number, used as the primary key.
        rng: Seeded generator, so output is reproducible.

    Returns:
        A COPY-formatted line, newline terminated.
    """
    name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {index}"
    return (
        f"{index}\t"
        f"SKU-{index:09d}\t"
        f"{name}\t"
        f"Seeded product {index}.\t"
        f"{rng.randrange(99, 500_000)}\t"
        f"{rng.choice(CATEGORIES)}\t"
        f"\\N\n"  # deleted_at -- NULL, every seeded row is live
    )


async def seed(count: int, seed_value: int) -> None:
    """Truncate and repopulate the products table.

    Args:
        count: Number of rows to write.
        seed_value: RNG seed, so a given count always yields the same rows.
    """
    settings = get_settings()
    engine = init_engine(settings)
    rng = random.Random(seed_value)  # noqa: S311 -- test data, not cryptography
    started = time.perf_counter()

    raw = await engine.raw_connection()
    try:
        driver_conn = raw.driver_connection
        if driver_conn is None:  # pragma: no cover -- defensive
            msg = "Could not reach the asyncpg connection."
            raise RuntimeError(msg)

        await driver_conn.execute("TRUNCATE TABLE products RESTART IDENTITY")

        written = 0
        while written < count:
            chunk = min(_COPY_CHUNK_ROWS, count - written)
            buffer = io.BytesIO("".join(_row(written + i + 1, rng) for i in range(chunk)).encode())
            await driver_conn.copy_to_table(
                "products",
                source=buffer,
                columns=[
                    "id",
                    "sku",
                    "name",
                    "description",
                    "price_cents",
                    "category",
                    "deleted_at",
                ],
                format="text",
            )
            written += chunk
            sys.stdout.write(f"\r  {written:,} / {count:,} rows")
            sys.stdout.flush()

        # The sequence is now behind the highest explicit id; without this the
        # first real insert collides on the primary key.
        await driver_conn.execute(
            "SELECT setval('products_id_seq', (SELECT MAX(id) FROM products))"
        )
    finally:
        raw.close()

    async with engine.connect() as conn:
        # ANALYZE, not just VACUUM: without fresh statistics the planner may
        # choose a sequential scan, and the baseline would measure the wrong
        # thing entirely.
        await conn.execute(text("ANALYZE products"))
        await conn.commit()

    elapsed = time.perf_counter() - started
    sys.stdout.write(f"\n  done in {elapsed:.1f}s ({count / elapsed:,.0f} rows/s)\n")
    await dispose_engine()


def main() -> int:
    """Parse arguments and run the seed.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Seed the products table.")
    parser.add_argument("--count", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    sys.stdout.write(f"Seeding {args.count:,} products (rng seed {args.seed})\n")
    asyncio.run(seed(args.count, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
