"""Collapse an arm's runs into the medians the benchmark docs report.

benchmarks/README.md fixes the reporting rule: 3 runs, median reported, k6
latency and service-side counters side by side. Doing that by eye across 24
result files invites transcription errors, so it is done here and the docs
quote this output.

Usage:
    python benchmarks/summarize.py                 # every arm found
    python benchmarks/summarize.py penetration     # arms matching a substring
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

RESULTS = pathlib.Path(__file__).resolve().parent / "results"


def median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def load(arm: str) -> tuple[list[dict], list[dict]]:
    k6 = [json.loads(p.read_text()) for p in sorted(RESULTS.glob(f"{arm}-run[0-9].json"))]
    svc = [json.loads(p.read_text()) for p in sorted(RESULTS.glob(f"{arm}-run[0-9]-metrics.json"))]
    return k6, svc


def arms() -> list[str]:
    names = set()
    for p in RESULTS.glob("*-run[0-9]-metrics.json"):
        names.add(p.name.rsplit("-run", 1)[0])
    return sorted(names)


def counter(totals: dict, prefix: str, label: str | None = None) -> float:
    """Sum one counter family, optionally only the series carrying `label`."""
    return sum(
        v for k, v in totals.items() if k.startswith(prefix) and (label is None or label in k)
    )


def _print_window(label: str, svc_runs: list[dict]) -> None:
    """Print the service-side counter totals for one time window."""
    qps = [r["windows"][label].get("db_qps") for r in svc_runs]
    qct = [r["windows"][label].get("db_queries") for r in svc_runs]
    print(f"  [{label}] db_queries per run: {qct}  median db_qps: {median(qps)}")


def report(arm: str) -> None:
    k6_runs, svc_runs = load(arm)
    if not svc_runs:
        print(f"\n## {arm}\n  (no results)")
        return

    print(f"\n## {arm}")
    print(f"  runs: {len(svc_runs)} service / {len(k6_runs)} k6")

    settings = svc_runs[0]["effective_settings"]
    print(
        "  effective: "
        f"ttl={settings['cache_ttl_seconds']}s jitter={settings['cache_ttl_jitter_pct']}% "
        f"bloom={settings['bloom_enabled']} coalesce={settings['coalesce_enabled']} "
        f"invalidate={settings['cache_invalidate_on_write']} workers={settings['web_concurrency']}"
    )

    bad = [r["run"] for r in svc_runs if r["k6_exit_code"] != 0]
    if bad:
        print(f"  !! non-zero k6 exit on run(s) {bad}")

    # --- service-side counters, per window -----------------------------------
    for label in svc_runs[0]["windows"]:
        _print_window(label, svc_runs)

        hits = [
            counter(
                r["windows"][label].get("totals", {}),
                "cache_gateway_cache_operations_total",
                'outcome="hit"',
            )
            for r in svc_runs
        ]
        miss = [
            counter(
                r["windows"][label].get("totals", {}),
                "cache_gateway_cache_operations_total",
                'outcome="miss"',
            )
            for r in svc_runs
        ]
        rej = [
            counter(
                r["windows"][label].get("totals", {}),
                "cache_gateway_cache_operations_total",
                'outcome="bloom_reject"',
            )
            for r in svc_runs
        ]
        bmiss = [
            counter(
                r["windows"][label].get("totals", {}),
                "cache_gateway_bloom_queries_total",
                'result="miss"',
            )
            for r in svc_runs
        ]
        bhit = [
            counter(
                r["windows"][label].get("totals", {}),
                "cache_gateway_bloom_queries_total",
                'result="hit"',
            )
            for r in svc_runs
        ]
        coal = [
            counter(r["windows"][label].get("totals", {}), "cache_gateway_coalesced_requests_total")
            for r in svc_runs
        ]
        if any(hits) or any(miss):
            tot = [h + m for h, m in zip(hits, miss, strict=True)]
            rate = [round(100 * h / t, 2) if t else None for h, t in zip(hits, tot, strict=True)]
            print(
                f"    cache hit/miss: {[(int(h), int(m)) for h, m in zip(hits, miss, strict=True)]}"
                f"  hit rate median: {median(rate)}%"
            )
        if any(rej):
            print(f"    bloom_reject (cache op): {[int(x) for x in rej]}")
        # A Bloom "hit" means "might exist". For a key that really does exist
        # that is a true positive, so hits/(hits+misses) is only a false
        # positive rate when every key queried is known to be absent -- which
        # is true of the penetration scenario and of nothing else.
        if (any(bmiss) or any(bhit)) and arm.startswith("penetration"):
            fp = [
                round(100 * h / (h + m), 3) if (h + m) else None
                for h, m in zip(bhit, bmiss, strict=True)
            ]
            print(
                "    bloom miss/hit: "
                f"{[(int(m), int(h)) for m, h in zip(bmiss, bhit, strict=True)]}"
                f"  measured FP rate median: {median(fp)}%"
            )
        if any(coal):
            print(f"    coalesced requests: {[int(x) for x in coal]}")

    _print_k6(k6_runs)


def _print_k6(k6_runs: list[dict]) -> None:
    """Print the client-side latency and scenario-specific k6 numbers."""
    if not k6_runs:
        return
    res = [r["results"] for r in k6_runs]
    lat = [r.get("latency_ms") for r in res if r.get("latency_ms")]
    if lat:
        print(f"    k6 rps median: {median([r.get('rps') for r in res])}")
        for stat in ("med", "p95", "p99", "max"):
            print(
                f"    k6 {stat:>3}: {[x[stat] for x in lat]}"
                f"  median {median([x[stat] for x in lat])}"
            )
        print(f"    k6 error_rate: {[r.get('error_rate') for r in res]}")

    # consistency-specific
    if "reads" in res[0]:
        sp = [r["reads"]["stale_pct"] for r in res]
        raw = [r["read_after_write"]["stale_pct"] for r in res]
        print(f"    stale reads %: {sp}  median {median(sp)}")
        print(f"    read-after-write stale %: {raw}  median {median(raw)}")
        ages = [r["reads"]["value_age_ms"] for r in res if r["reads"]["value_age_ms"]]
        if ages:
            for stat in ("med", "p95", "p99", "max"):
                print(
                    f"    value age {stat:>3} ms: {[a[stat] for a in ages]}"
                    f"  median {median([a[stat] for a in ages])}"
                )


def main() -> int:
    wanted = sys.argv[1:]
    for arm in arms():
        if wanted and not any(w in arm for w in wanted):
            continue
        report(arm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
