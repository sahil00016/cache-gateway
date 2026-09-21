"""Gunicorn configuration.

Worker count is read from WEB_CONCURRENCY and is a measured decision, not a
default: the Bloom filter and the request coalescer live in process memory, so
this number changes what those protections can guarantee. See failure mode 3
in the project design document.
"""

import os

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
