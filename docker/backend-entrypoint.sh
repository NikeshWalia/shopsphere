#!/usr/bin/env sh
#
# Backend container entrypoint.
#
# Applies migrations and seeds before serving. Both are idempotent, so a
# restarted or replicated container converges on the same state rather than
# duplicating data.
#
# Doing this in the entrypoint is a deliberate simplification for a demo stack:
# it means `docker compose up` produces a working, populated application with no
# second command. A production deployment would run migrations as a separate
# job so that N replicas do not all race to migrate - noted in the README's
# "Known limitations".
set -eu

echo "[entrypoint] waiting for the database..."
python - <<'PY'
import os
import sys
import time

import psycopg

dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
deadline = time.monotonic() + 90

while time.monotonic() < deadline:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            print("[entrypoint] database is accepting connections")
            sys.exit(0)
    except psycopg.OperationalError as exc:
        print(f"[entrypoint] not ready yet ({type(exc).__name__}); retrying...")
        time.sleep(2)

print("[entrypoint] database did not become available within 90s", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] applying migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-true}" = "true" ]; then
    echo "[entrypoint] seeding..."
    python -m app.seed.seed
fi

echo "[entrypoint] starting: $*"
exec "$@"
