"""Prometheus metrics.

Metric names are chosen now, before there is anything to measure, because
renaming a metric after dashboards and alerts reference it is expensive.

Multi-process scraping
----------------------
``prometheus_client`` keeps counters in **process memory**. Under gunicorn with
N workers that means N independent registries, and a scrape is answered by
whichever worker the request happens to reach -- so ``/metrics`` reports one
worker's slice, not the service's total.

This was observed, not theorised: eight consecutive scrapes during a benchmark
returned 688, 688, 346, 688, 688, 216, 688, 346. A counter cannot decrease.
Three workers were answering, each reporting only what it had seen.

That is fatal here specifically, because ``database_queries_total`` is the
headline number of this entire project -- every protection added later exists
to make it smaller, and a quarter of the truth would have made every
before/after comparison meaningless.

The fix is ``prometheus_client``'s multiprocess mode: when
``PROMETHEUS_MULTIPROC_DIR`` is set, values are written to memory-mapped files
that a scrape aggregates across every live worker. See ADR-0007.
"""

import os

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, multiprocess

MULTIPROCESS_ENABLED = "PROMETHEUS_MULTIPROC_DIR" in os.environ
"""True when running under gunicorn with the multiprocess directory configured.

False for a single-process uvicorn dev server and in tests, where the ordinary
in-memory registry is both correct and simpler.
"""

REGISTRY = CollectorRegistry(auto_describe=True)

http_requests_total = Counter(
    "cache_gateway_http_requests_total",
    "HTTP requests handled, by method, path template and status.",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "cache_gateway_http_request_duration_seconds",
    "End-to-end request duration.",
    labelnames=("method", "path"),
    # Buckets straddle the p99 targets in the design document (5ms hit,
    # 50ms miss) so the SLO is directly readable off the histogram.
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    registry=REGISTRY,
)

cache_operations_total = Counter(
    "cache_gateway_cache_operations_total",
    "Cache lookups by outcome.",
    labelnames=("outcome",),  # hit | miss | bloom_reject | coalesced
    registry=REGISTRY,
)

database_queries_total = Counter(
    "cache_gateway_database_queries_total",
    "Queries issued to Postgres. The headline number of this project.",
    labelnames=("operation",),
    registry=REGISTRY,
)

# Gauges need an explicit aggregation rule across processes. `livesum` adds up
# the live workers' values, which is the meaningful total: with N workers there
# are genuinely N Bloom filters and N coalescer maps, and the sum is how many
# items and in-flight keys the service is really holding.
bloom_filter_items = Gauge(
    "cache_gateway_bloom_filter_items",
    "Items held in the in-process Bloom filters, summed across live workers.",
    registry=REGISTRY,
    multiprocess_mode="livesum",
)

inflight_coalesced_keys = Gauge(
    "cache_gateway_inflight_coalesced_keys",
    "Keys with an in-flight load that other requests are waiting on.",
    registry=REGISTRY,
    multiprocess_mode="livesum",
)


def build_scrape_registry() -> CollectorRegistry:
    """Return the registry a scrape should render.

    In multiprocess mode a *fresh* registry is built per scrape and populated
    by ``MultiProcessCollector``, which reads every worker's mmap files and
    aggregates them. Rendering the module-level registry instead would report
    only the worker that happened to serve the request.

    Returns:
        The registry to render for this scrape.
    """
    if not MULTIPROCESS_ENABLED:
        return REGISTRY

    registry = CollectorRegistry()
    # prometheus_client ships partial stubs; MultiProcessCollector is untyped
    # upstream. Narrowly ignored here rather than loosening mypy for the module.
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return registry
