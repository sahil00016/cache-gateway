"""Benchmark runner: sets up an arm, measures the service, runs k6.

Why this exists
---------------
k6 measures the client side. The headline number of this project is database
QPS, which only the service can report (see benchmarks/README.md). The earlier
approach -- inferring cache hits and Bloom rejects from response latency inside
the k6 script -- cannot distinguish a Bloom reject from a fast Redis negative
lookup and degrades further under load. So the authoritative counters are read
from the service's own /metrics, here, and committed alongside the k6 output.

Sampling is continuous at 1 Hz rather than a before/after pair, for two
reasons: it lets any sub-window be sliced out after the fact (k6 runs its
warm-up and measured phases inside one process, so a single pre/post delta
would blend them), and the per-second series *is* the avalanche result -- a
spike and a smoothed curve are the same totals with different shapes.

Usage:
    python benchmarks/run.py --list
    python benchmarks/run.py penetration-protected --runs 3
    python benchmarks/run.py --all --runs 3
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
BASE_URL = os.environ.get("BENCH_BASE_URL", "http://localhost:8010")

SAMPLE_INTERVAL_S = 1.0

# Counters worth keeping. Everything else on /metrics is noise for this report.
TRACKED = (
    "cache_gateway_database_queries_total",
    "cache_gateway_cache_operations_total",
    "cache_gateway_bloom_queries_total",
    "cache_gateway_coalesced_requests_total",
    "cache_gateway_http_requests_total",
)

_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>\S+)$"
)


# --------------------------------------------------------------- arms --------

#: Each arm is one bar in a before/after table. `service` is the environment the
#: API container is restarted with; `k6` is passed to the scenario. `phases`
#: names the sub-windows to aggregate, as (label, start_s, end_s) offsets from
#: the moment k6 starts, so the measured phase can be separated from warm-up.
ARMS: dict[str, dict] = {
    # -- M4 penetration: 10s warm-up, then 60s measured -----------------------
    "penetration-unprotected": {
        "scenario": "penetration.js",
        "service": {"BENCH_BLOOM_ENABLED": "false"},
        "k6": {"RATE": "400", "DURATION": "60s", "DATASET": "1000000"},
        "phases": [("measured", 10, 70)],
    },
    "penetration-protected": {
        "scenario": "penetration.js",
        "service": {"BENCH_BLOOM_ENABLED": "true"},
        "k6": {"RATE": "400", "DURATION": "60s", "DATASET": "1000000"},
        "phases": [("measured", 10, 70)],
    },
    # -- M5 avalanche: 30s warm, then 90s hold across a 60s TTL ---------------
    # TTL is pinned to 60s so the expiry wave lands inside the hold window.
    "avalanche-no-jitter": {
        "scenario": "avalanche.js",
        "service": {"BENCH_CACHE_TTL_JITTER_PCT": "0", "BENCH_CACHE_TTL_SECONDS": "60"},
        "k6": {"WARMUP_KEYS": "1000", "RATE": "400", "HOLD_DURATION": "90s", "TTL_SECONDS": "60"},
        "phases": [("measured", 30, 120), ("expiry_window", 55, 80)],
        # The warm phase writes all 1,000 keys inside the first ~5s, not at
        # the 30s mark where the measured phase begins, so a 60s TTL expires
        # them around t=61-67 (plus up to 6s of jitter in the other arm).
        # The original 85-105 window sat well past the wave and captured
        # only steady state.
    },
    "avalanche-with-jitter": {
        "scenario": "avalanche.js",
        "service": {"BENCH_CACHE_TTL_JITTER_PCT": "10", "BENCH_CACHE_TTL_SECONDS": "60"},
        "k6": {"WARMUP_KEYS": "1000", "RATE": "400", "HOLD_DURATION": "90s", "TTL_SECONDS": "60"},
        "phases": [("measured", 30, 120), ("expiry_window", 55, 80)],
        # The warm phase writes all 1,000 keys inside the first ~5s, not at
        # the 30s mark where the measured phase begins, so a 60s TTL expires
        # them around t=61-67 (plus up to 6s of jitter in the other arm).
        # The original 85-105 window sat well past the wave and captured
        # only steady state.
    },
    # -- M6 stampede: sustained concurrency on one key, ~12 expiries per run --
    # A 5s TTL with jitter off makes expiry crisp and repeated; 200 VUs with no
    # sleep keep 200 requests in flight so every expiry meets a real stampede.
    # The earlier one-shot 500-VU burst never formed one -- see stampede.js.
    "stampede-no-coalesce": {
        "scenario": "stampede.js",
        "service": {
            "BENCH_COALESCE_ENABLED": "false",
            "BENCH_CACHE_TTL_SECONDS": "5",
            "BENCH_CACHE_TTL_JITTER_PCT": "0",
        },
        "k6": {"HOT_KEY": "42", "TTL": "5", "BURST_VUS": "200", "DURATION": "60s"},
        "phases": [("measured", 0, 60)],
    },
    "stampede-with-coalesce": {
        "scenario": "stampede.js",
        "service": {
            "BENCH_COALESCE_ENABLED": "true",
            "BENCH_CACHE_TTL_SECONDS": "5",
            "BENCH_CACHE_TTL_JITTER_PCT": "0",
        },
        "k6": {"HOT_KEY": "42", "TTL": "5", "BURST_VUS": "200", "DURATION": "60s"},
        "phases": [("measured", 0, 60)],
    },
    # -- M7 consistency: 60s of concurrent readers and writers ----------------
    "consistency-no-invalidation": {
        "scenario": "consistency.js",
        "service": {"BENCH_CACHE_INVALIDATE_ON_WRITE": "false", "BENCH_CACHE_TTL_SECONDS": "60"},
        "k6": {"DURATION": "60s", "HOT_KEYS": "100"},
        "phases": [("measured", 0, 60)],
    },
    "consistency-with-invalidation": {
        "scenario": "consistency.js",
        "service": {"BENCH_CACHE_INVALIDATE_ON_WRITE": "true", "BENCH_CACHE_TTL_SECONDS": "60"},
        "k6": {"DURATION": "60s", "HOT_KEYS": "100"},
        "phases": [("measured", 0, 60)],
    },
}

#: What each arm's service environment must read back as on /v1/admin/stats.
#: Settings are verified from the service, never assumed from what we exported
#: -- a typo in an env var name is silent otherwise (ADR-0002).
SETTINGS_KEY = {
    "BENCH_BLOOM_ENABLED": ("bloom_enabled", lambda v: v == "true"),
    "BENCH_COALESCE_ENABLED": ("coalesce_enabled", lambda v: v == "true"),
    "BENCH_CACHE_INVALIDATE_ON_WRITE": ("cache_invalidate_on_write", lambda v: v == "true"),
    "BENCH_CACHE_TTL_JITTER_PCT": ("cache_ttl_jitter_pct", int),
    "BENCH_CACHE_TTL_SECONDS": ("cache_ttl_seconds", int),
    "BENCH_WEB_CONCURRENCY": ("web_concurrency", int),
}


# ------------------------------------------------------------- plumbing ------


def log(msg: str) -> None:
    print(f"[run] {msg}", flush=True)


def fetch(path: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode()


def parse_metrics(text: str) -> dict[str, float]:
    """Flatten the Prometheus exposition into {name{labels}: value}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if not any(name.startswith(t) for t in TRACKED):
            continue
        try:
            out[name + (m.group("labels") or "")] = float(m.group("value"))
        except ValueError:
            continue
    return out


class Sampler(threading.Thread):
    """Poll /metrics at a fixed interval until stopped."""

    def __init__(self) -> None:
        """Create the sampler with an empty series and a clear stop flag."""
        super().__init__(daemon=True)
        self.samples: list[tuple[float, dict[str, float]]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        """Scrape until stopped, appending one timestamped sample per tick."""
        while not self._halt.is_set():
            tick = time.time()
            # A dropped scrape is a gap in the series, not a failed run, and
            # the gap is already visible in the timestamps. Logging it here
            # would interleave with k6's own progress output for no gain.
            with contextlib.suppress(urllib.error.URLError, TimeoutError, OSError):
                self.samples.append((tick, parse_metrics(fetch("/metrics", timeout=3.0))))
            self._halt.wait(max(0.0, SAMPLE_INTERVAL_S - (time.time() - tick)))

    def stop(self) -> None:
        """Signal the loop to end and wait briefly for the thread to join."""
        self._halt.set()
        self.join(timeout=5)


def compose(*args: str, env: dict | None = None) -> None:
    cmd = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(ROOT / "docker-compose.bench.yml"),
        *args,
    ]
    subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        env={**os.environ, **(env or {})},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_ready(deadline_s: float = 180.0) -> None:
    """Block until /readyz reports every dependency healthy.

    The Bloom filter is built from a 1M-row table at startup, so a restart is
    not instant and a run started too early measures a half-built filter.
    """
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            if json.loads(fetch("/readyz", timeout=3.0)).get("ready"):
                return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
            pass  # still booting
        time.sleep(2)
    raise RuntimeError("service did not become ready")


def flush_redis() -> None:
    """Start every arm from a cold cache, so arms are comparable."""
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "FLUSHALL"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def verify_settings(service_env: dict[str, str]) -> dict:
    """Read effective settings back from the service and assert they took.

    Exporting an env var proves nothing: a misspelled name, a stale container
    or a compose override that did not apply all fail silently and produce a
    run that measures the wrong configuration while looking perfectly healthy.
    """
    stats = json.loads(fetch("/v1/admin/stats"))["data"]
    for var, want_raw in service_env.items():
        if var not in SETTINGS_KEY:
            continue
        key, cast = SETTINGS_KEY[var]
        want = cast(want_raw)
        got = stats[key]
        if got != want:
            raise RuntimeError(
                f"{var}={want_raw} did not take effect: /v1/admin/stats reports "
                f"{key}={got!r}, expected {want!r}"
            )
    return stats


# -------------------------------------------------------------- analysis ----


def delta(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """Counter increase between two samples, dropping unchanged series."""
    out = {}
    for k, v in b.items():
        d = v - a.get(k, 0.0)
        if d:
            out[k] = round(d, 3)
    return out


def window(samples: list[tuple[float, dict]], t0: float, start_s: float, end_s: float) -> dict:
    """Aggregate counter deltas across one time window."""
    inside = [(t, s) for t, s in samples if t0 + start_s <= t <= t0 + end_s]
    if len(inside) < 2:
        return {"error": "not enough samples in window", "samples": len(inside)}
    first, last = inside[0], inside[-1]
    span = last[0] - first[0]
    totals = delta(first[1], last[1])
    db = sum(v for k, v in totals.items() if k.startswith("cache_gateway_database_queries_total"))
    return {
        "window_s": round(span, 2),
        "samples": len(inside),
        "totals": totals,
        "db_queries": round(db, 1),
        "db_qps": round(db / span, 1) if span else None,
    }


def series(samples: list[tuple[float, dict]], t0: float, prefix: str) -> list[dict]:
    """Per-second rate of one counter family, for plotting the shape."""
    out = []
    for (ta, a), (tb, b) in itertools.pairwise(samples):
        span = tb - ta
        if span <= 0:
            continue
        inc = sum(v for k, v in delta(a, b).items() if k.startswith(prefix))
        out.append({"t": round(tb - t0, 2), "rate": round(inc / span, 1)})
    return out


# ------------------------------------------------------------------ run -----


def run_arm(arm: str, run_no: int) -> dict:
    spec = ARMS[arm]
    log(f"--- {arm} run {run_no} ---")

    service_env = spec["service"]
    log(f"restarting api with {service_env or '(defaults)'}")
    compose("up", "-d", "--no-deps", "api", env=service_env)
    wait_ready()

    stats = verify_settings(service_env)
    log(
        f"effective: ttl={stats['cache_ttl_seconds']}s jitter={stats['cache_ttl_jitter_pct']}% "
        f"bloom={stats['bloom_enabled']} coalesce={stats['coalesce_enabled']} "
        f"invalidate={stats['cache_invalidate_on_write']} workers={stats['web_concurrency']}"
    )

    flush_redis()
    log("redis flushed; starting sampler")

    sampler = Sampler()
    sampler.start()
    time.sleep(2)  # a couple of baseline samples before load starts

    k6_env = {**os.environ, "BASE_URL": BASE_URL}
    for k, v in spec["k6"].items():
        k6_env[k] = v
    cmd = ["k6", "run", "-e", f"SCENARIO={arm}", "-e", f"RUN={run_no}"]
    for k, v in spec["k6"].items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["-e", f"BASE_URL={BASE_URL}", str(ROOT / "benchmarks" / "scenarios" / spec["scenario"])]

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, env=k6_env, capture_output=True, text=True, check=False)
    t_end = time.time()
    time.sleep(2)  # let the final increments land in a sample
    sampler.stop()

    if proc.returncode != 0:
        log(f"k6 exited {proc.returncode}")
        log(proc.stderr[-2000:])

    out = {
        "arm": arm,
        "run": run_no,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "k6_exit_code": proc.returncode,
        "duration_s": round(t_end - t0, 2),
        "effective_settings": stats,
        "service_env": service_env,
        "windows": {label: window(sampler.samples, t0, a, b) for label, a, b in spec["phases"]},
        "db_qps_series": series(sampler.samples, t0, "cache_gateway_database_queries_total"),
        "cache_ops_series": series(sampler.samples, t0, "cache_gateway_cache_operations_total"),
        "totals_whole_run": delta(sampler.samples[0][1], sampler.samples[-1][1])
        if len(sampler.samples) >= 2
        else {},
    }

    path = RESULTS / f"{arm}-run{run_no}-metrics.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    log(f"wrote {path.relative_to(ROOT)}")

    for label, w in out["windows"].items():
        log(f"  {label}: db_queries={w.get('db_queries')} db_qps={w.get('db_qps')}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arms", nargs="*")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name in ARMS:
            print(name)
        return 0

    targets = list(ARMS) if args.all else args.arms
    if not targets:
        ap.error("name at least one arm, or pass --all")
    for arm in targets:
        if arm not in ARMS:
            ap.error(f"unknown arm {arm!r}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    for arm in targets:
        for run_no in range(1, args.runs + 1):
            run_arm(arm, run_no)
    return 0


if __name__ == "__main__":
    sys.exit(main())
