"""Gunicorn configuration.

Worker count is read from WEB_CONCURRENCY and is a measured decision, not a
default: the Bloom filter and the request coalescer live in process memory, so
this number changes what those protections can guarantee. See failure mode 3
in the project design document.
"""

import os
import shutil
from pathlib import Path

# --- Prometheus multiprocess mode ---
# Set here, in the master, BEFORE any worker is forked and before the app (and
# therefore prometheus_client) is imported. Without this each worker keeps its
# own in-memory counters and /metrics reports whichever worker answered -- see
# ADR-0007 and the note in src/core/metrics.py.
# /dev/shm is tmpfs, so the mmap files never touch disk -- they are written on
# every metric update and durability is worthless for them. It is world-
# writable like /tmp, so the directory is created 0700 below and owned by the
# service user; inside a container the mount is namespace-isolated as well.
_DEFAULT_MULTIPROC_DIR = "/dev/shm/cache-gateway-metrics"  # noqa: S108 -- created 0700, see above
_MULTIPROC_DIR = Path(os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", _DEFAULT_MULTIPROC_DIR))


def on_starting(server):  # noqa: ANN001, ANN201, ARG001 -- gunicorn hook signature
    """Start from an empty metrics directory.

    Stale mmap files from a previous run belong to dead PIDs and would be
    aggregated into this run's totals, inflating every counter at boot.
    """
    if _MULTIPROC_DIR.exists():
        shutil.rmtree(_MULTIPROC_DIR, ignore_errors=True)
    _MULTIPROC_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def child_exit(server, worker):  # noqa: ANN001, ANN201, ARG001 -- gunicorn hook signature
    """Retire a dead worker's metrics files.

    Without this, a worker recycled by max_requests leaves its mmap files
    behind and its counters are double-counted against the replacement's.
    """
    from prometheus_client import multiprocess  # noqa: PLC0415 -- optional at import time

    multiprocess.mark_process_dead(worker.pid)


# --- binding ---
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# --- workers ---
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", "4"))

# --- timeouts ---
timeout = 30
graceful_timeout = 30
keepalive = 5

# --- lifecycle ---
# Restart workers periodically so a slow leak cannot accumulate indefinitely.
# Jittered so all workers do not restart at once -- the same avalanche problem
# this service exists to demonstrate, applied to its own process management.
max_requests = 10_000
max_requests_jitter = 1_000

# --- logging ---
# Access logs are emitted by the application's own middleware in JSON, so
# gunicorn's plain-text access log is disabled to avoid duplicate, worse lines.
accesslog = None
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

preload_app = False
"""Must stay False.

Preloading forks workers from one parent, which would share the parent's
already-built Bloom filter copy-on-write. That sounds like a saving, but it
makes the per-worker memory accounting invisible and breaks the measurement
this project is built to produce.
"""
