# syntax=docker/dockerfile:1
#
# ShopSphere API.
#
# Multi-stage so the runtime image carries only the virtualenv and the source -
# no compilers, no build caches, no test tooling.

# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencies are installed before the source is copied so that editing code
# does not invalidate the (slow) dependency layer.
COPY backend/requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

# curl is used by the container healthcheck below. Nothing else is added.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Runs as a non-root user: a container compromise should not also be root.
RUN useradd --create-home --uid 1000 shopsphere

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=shopsphere:shopsphere backend/app ./app
COPY --chown=shopsphere:shopsphere backend/migrations ./migrations
COPY --chown=shopsphere:shopsphere backend/alembic.ini ./alembic.ini
COPY --chown=shopsphere:shopsphere docker/backend-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER shopsphere
EXPOSE 8000

# Hits the readiness probe, so the container is only "healthy" once it can
# actually reach the database - which is what dependent services wait on.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD curl -fsS http://localhost:8000/health/ready || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
