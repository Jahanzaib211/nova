"""Job worker process entrypoint.

    python -m app.jobs.worker [--queues a,b] [--concurrency N] [--worker-id ID]

Loads config.yaml the same way the gateway does, initialises the persistence
engine, registers handlers and serves until SIGTERM/SIGINT, then waits
``jobs.shutdown_grace_seconds`` for in-flight jobs and releases the rest.
Never imports FastAPI: this process must outlive gateway reloads.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
from datetime import timedelta

from deerflow.config.app_config import apply_logging_level, get_app_config
from deerflow.jobs.worker import Worker, WorkerSettings
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.job.sql import JobRepository

from .handlers import build_registry

logger = logging.getLogger("app.jobs.worker")


def settings_from_config(args: argparse.Namespace) -> WorkerSettings:
    cfg = get_app_config().jobs
    queues = [q.strip() for q in (args.queues or ",".join(cfg.queues)).split(",") if q.strip()]
    return WorkerSettings(
        worker_id=args.worker_id or cfg.worker_id or f"{socket.gethostname()}-{os.getpid()}",
        queues=queues,
        concurrency=args.concurrency or cfg.concurrency,
        lease_ttl=timedelta(seconds=cfg.lease_ttl_seconds),
        poll_interval=cfg.poll_interval_seconds,
        reaper_interval=cfg.reaper_interval_seconds,
        scheduler_interval=cfg.scheduler_interval_seconds,
        shutdown_grace=cfg.shutdown_grace_seconds,
        version=os.environ.get("NOVA_VERSION", ""),
    )


async def serve(args: argparse.Namespace) -> int:
    config = get_app_config()
    apply_logging_level(config.log_level)
    if not config.jobs.enabled and not args.force:
        logger.error("jobs.enabled is false in config.yaml; refusing to start (use --force to override)")
        return 2
    await init_engine_from_config(config.database)
    sf = get_session_factory()
    if sf is None:
        logger.error("database.backend is 'memory'; the job runner needs sqlite or postgres")
        return 2
    worker = Worker(JobRepository(sf), build_registry(), settings_from_config(args))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    logger.info("job worker starting (queues=%s)", ",".join(settings_from_config(args).queues))
    try:
        await worker.serve()
    finally:
        await close_engine()
    logger.info("job worker stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queues", default=None, help="comma-separated queue names (default: config jobs.queues)")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--force", action="store_true", help="start even if jobs.enabled is false")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(serve(args))


if __name__ == "__main__":
    raise SystemExit(main())
