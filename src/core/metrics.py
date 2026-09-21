"""Prometheus metrics.

Metric names are chosen now, before there is anything to measure, because
renaming a metric after dashboards and alerts reference it is expensive.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

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

bloom_filter_items = Gauge(
    "cache_gateway_bloom_filter_items",
    "Items currently held in the in-process Bloom filter.",
    registry=REGISTRY,
)

inflight_coalesced_keys = Gauge(
    "cache_gateway_inflight_coalesced_keys",
    "Keys with an in-flight load that other requests are waiting on.",
    registry=REGISTRY,
)
