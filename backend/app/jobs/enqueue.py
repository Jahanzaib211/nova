"""Enqueue a job from the command line (smoke tests, `make jobs-demo`).

python -m app.jobs.enqueue jobs.demo.sleep '{"seconds": 3}' [--queue default] [--wait]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from deerflow.config.app_config import get_app_config
from deerflow.jobs.queue import JobQueue
from deerflow.jobs.status import is_terminal
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.job.sql import JobRepository


async def run(args: argparse.Namespace) -> int:
    await init_engine_from_config(get_app_config().database)
    try:
        sf = get_session_factory()
        if sf is None:
            print("database.backend is 'memory'; nothing to enqueue into")
            return 2
        repo = JobRepository(sf)
        job_id = await JobQueue(repo).enqueue(args.type, json.loads(args.payload), queue=args.queue)
        print(job_id)
        if not args.wait:
            return 0
        for _ in range(args.timeout):
            job = await repo.get(job_id)
            if job and is_terminal(job["status"]):
                print(json.dumps({"status": job["status"], "result": job["result"], "error": job["error"]}, default=str))
                return 0 if job["status"] == "succeeded" else 1
            await asyncio.sleep(1)
        print("timed out waiting for the job")
        return 1
    finally:
        await close_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("type")
    parser.add_argument("payload", nargs="?", default="{}")
    parser.add_argument("--queue", default="default")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
