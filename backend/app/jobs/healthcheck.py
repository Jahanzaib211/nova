"""Container healthcheck: is a worker row fresh?

    python -m app.jobs.healthcheck [--max-age 90]

Exit 0 when some job_workers row was updated within ``--max-age`` seconds.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from deerflow.config.app_config import get_app_config
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.job.sql import JobRepository


async def check(max_age: float) -> bool:
    await init_engine_from_config(get_app_config().database)
    try:
        sf = get_session_factory()
        if sf is None:
            return False
        workers = await JobRepository(sf).list_workers()
        now = datetime.now(UTC)
        return any((now - w["last_seen_at"]).total_seconds() <= max_age for w in workers)
    finally:
        await close_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age", type=float, default=90.0)
    args = parser.parse_args(argv)
    return 0 if asyncio.run(check(args.max_age)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
