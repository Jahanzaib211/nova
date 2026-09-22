#!/usr/bin/env bash
# Apply Alembic migrations to the Nova application database.
#
# Runs from backend/ (inside the gateway container at boot, or on the host
# via `make db-migrate`). Two things the raw `alembic` CLI does not do:
#
#   1. Normalise the URL. The stack's DATABASE_URL is the plain
#      `postgresql://` form config.yaml expects; migrations/env.py opens it
#      with create_async_engine, which needs the `+asyncpg` driver.
#   2. Create missing tables first. The version chain only alters tables that
#      already exist (its first version alters `runs` and fails on an empty
#      database) — Base.metadata.create_all is what creates new tables, so
#      it runs first, then `upgrade head` applies column-level changes.
#      Every version is inspect()-guarded, so the first run on a live
#      database that never had alembic_version is a no-op that stamps head.
#
# See backend/tests/test_migrations_apply_clean.py for the pinned contract.
set -euo pipefail

# In the container the script is bind-mounted to /usr/local/bin, so the
# repo-relative guess does not apply there; NOVA_BACKEND_DIR wins.
if [ -n "${NOVA_BACKEND_DIR:-}" ]; then
  BACKEND_DIR="$NOVA_BACKEND_DIR"
elif [ -d "$(dirname "${BASH_SOURCE[0]}")/../backend" ]; then
  BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
else
  BACKEND_DIR=/app/backend
fi
cd "$BACKEND_DIR"

url="${DEER_FLOW_DATABASE_URL:-${DATABASE_URL:-}}"
if [ -z "$url" ]; then
  echo "[db-migrate] no DATABASE_URL / DEER_FLOW_DATABASE_URL; nothing to migrate" >&2
  exit 0
fi
case "$url" in
  postgresql://*) url="postgresql+asyncpg://${url#postgresql://}" ;;
  postgres://*)   url="postgresql+asyncpg://${url#postgres://}" ;;
  sqlite://*)     url="sqlite+aiosqlite://${url#sqlite://}" ;;
esac
export DEER_FLOW_DATABASE_URL="$url"

echo "[db-migrate] create_all (new tables) …" >&2
PYTHONPATH=. uv run --no-sync python - <<'PY'
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
import deerflow.persistence.models  # noqa: F401
from deerflow.persistence.base import Base

async def main():
    engine = create_async_engine(os.environ["DEER_FLOW_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

asyncio.run(main())
PY

echo "[db-migrate] alembic upgrade head …" >&2
PYTHONPATH=. uv run --no-sync alembic \
  -c packages/harness/deerflow/persistence/migrations/alembic.ini upgrade head
echo "[db-migrate] done" >&2
