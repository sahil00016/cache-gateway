# syntax=docker/dockerfile:1.7
#
# Multi-stage build. The builder compiles wheels; the runtime ships only the
# installed packages and the source, with no build toolchain present.

# --------------------------------------------------------------------------- #
# Stage 1: builder
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements/base.txt ./requirements/base.txt

# --require-hashes: every package must match a hash pinned in base.txt, so a
# compromised or substituted artefact fails the build rather than shipping.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes -r requirements/base.txt

# --------------------------------------------------------------------------- #
# Stage 2: runtime
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# libpq5 only -- asyncpg needs the client library, not the build headers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser alembic.ini docker/gunicorn.conf.py ./

USER appuser
EXPOSE 8000

# Liveness only -- readiness is the orchestrator's job and must not restart
# the container when a dependency is merely unreachable.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "src.main:app"]
