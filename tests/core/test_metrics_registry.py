"""Tests for metric scraping under single- and multi-process servers.

Written after a real defect: eight consecutive /metrics scrapes during a
benchmark returned 688, 688, 346, 688, 688, 216, 688, 346. A counter cannot
decrease -- three gunicorn workers were each reporting only their own slice.
"""

import importlib

import pytest
from prometheus_client import CollectorRegistry

from src.core import metrics


def test_single_process_scrape_uses_the_module_registry() -> None:
    assert metrics.MULTIPROCESS_ENABLED is False
    assert metrics.build_scrape_registry() is metrics.REGISTRY


def test_multiprocess_scrape_builds_a_fresh_aggregating_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    # prometheus_client reads the env var when its value classes are imported,
    # so the module must be reloaded with the variable already set.
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    # Reload, not re-import: MULTIPROCESS_ENABLED is read at module import, so
    # the module must be re-executed with the variable already set.
    reloaded = importlib.reload(metrics)
    try:
        assert reloaded.MULTIPROCESS_ENABLED is True

        registry = reloaded.build_scrape_registry()

        # A fresh registry every scrape, never the module-level one: that is
        # what makes the scrape aggregate across workers instead of reporting
        # whichever process answered.
        assert isinstance(registry, CollectorRegistry)
        assert registry is not reloaded.REGISTRY
        assert reloaded.build_scrape_registry() is not registry
    finally:
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        importlib.reload(reloaded)


def test_gauges_declare_a_multiprocess_aggregation_mode() -> None:
    # Counters aggregate by summing automatically; gauges do not, and an
    # unspecified mode silently reports one arbitrary worker's value.
    for gauge in (metrics.bloom_filter_items, metrics.inflight_coalesced_keys):
        assert gauge._multiprocess_mode == "livesum"
