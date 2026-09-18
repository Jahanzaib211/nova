"""The job worker loop: claim → run → outcome, plus reaper, scheduler, self-heartbeat.

Runs in its own process (``python -m app.jobs.worker``), never inside the
gateway: a gateway ``--reload`` or restart must not kill a campaign send.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .context import JobContext
from .errors import JobCancelled, RetryableError
from .registry import JobRegistry
from .scheduler import tick_schedules

if TYPE_CHECKING:
    from deerflow.persistence.job.sql import JobRepository

logger = logging.getLogger(__name__)


@dataclass
class WorkerSettings:
    worker_id: str
    queues: list[str] = field(default_factory=lambda: ["default"])
    concurrency: int = 4
    lease_ttl: timedelta = timedelta(seconds=60)
    poll_interval: float = 1.0
    reaper_interval: float = 15.0
    scheduler_interval: float = 30.0
    worker_heartbeat_interval: float = 10.0
    shutdown_grace: float = 30.0
    version: str = ""


async def reap_expired_leases(repo: JobRepository, *, now: datetime | None = None, backoff_seconds: int | None = None) -> list[str]:
    """Return leases nobody heartbeats to the queue (retry) or dead-letter them."""
    reaped: list[str] = []
    for job_id in await repo.expired_leases(now=now):
        await repo.schedule_retry(job_id, error="lease expired: worker stopped heartbeating", backoff_seconds=backoff_seconds)
        reaped.append(job_id)
    return reaped


class Worker:
    def __init__(self, repo: JobRepository, registry: JobRegistry, settings: WorkerSettings) -> None:
        self._repo = repo
        self._registry = registry
        self._s = settings
        self._running: dict[str, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()

    # ── one job ──────────────────────────────────────────────────────────
    async def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        handler = self._registry.get(job["type"])
        if handler is None:
            await self._repo.mark_dead_letter(job_id, error=f"no handler registered for job type {job['type']!r}")
            return
        await self._repo.mark_running(job_id, worker_id=self._s.worker_id)
        ctx = JobContext(self._repo, job, worker_id=self._s.worker_id, lease_ttl=self._s.lease_ttl)

        async def auto_heartbeat() -> None:
            # Keeps the lease alive for handlers that never call ctx.heartbeat().
            interval = max(1.0, self._s.lease_ttl.total_seconds() / 3)
            while True:
                await asyncio.sleep(interval)
                await self._repo.heartbeat(job_id, worker_id=self._s.worker_id, lease_ttl=self._s.lease_ttl)

        hb = asyncio.create_task(auto_heartbeat())
        try:
            result = await handler(ctx)
            await self._repo.mark_succeeded(job_id, result=result)
        except JobCancelled:
            await self._repo.mark_cancelled(job_id)
        except RetryableError as exc:
            await self._repo.schedule_retry(job_id, error=str(exc), delay=exc.delay)
        except asyncio.CancelledError:
            # Worker shutting down mid-job: give it back untouched.
            await self._repo.release(job_id, worker_id=self._s.worker_id)
            raise
        except Exception as exc:  # noqa: BLE001 - any handler failure is the job's failure, not the worker's
            logger.exception("job %s (%s) failed", job_id, job["type"])
            await self._repo.mark_failed(job_id, error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}")
        finally:
            hb.cancel()

    async def _launch(self, job: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run_job(job))
        self._running[job["id"]] = task
        task.add_done_callback(lambda _t, jid=job["id"]: self._running.pop(jid, None))

    async def run_once(self) -> int:
        """Claim what capacity allows, run those jobs to completion. Returns the count."""
        free = self._s.concurrency - len(self._running)
        if free <= 0:
            return 0
        jobs = await self._repo.claim(queues=self._s.queues, worker_id=self._s.worker_id, lease_ttl=self._s.lease_ttl, limit=free)
        for job in jobs:
            await self._launch(job)
        if jobs:
            await asyncio.gather(*[self._running[j["id"]] for j in jobs if j["id"] in self._running], return_exceptions=True)
        return len(jobs)

    # ── housekeeping ─────────────────────────────────────────────────────
    async def announce(self) -> None:
        await self._repo.upsert_worker(
            worker_id=self._s.worker_id,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            queues=list(self._s.queues),
            version=self._s.version,
            current_job_ids=sorted(self._running),
        )

    async def _loop(self, name: str, interval: float, fn) -> None:
        while not self._stopping.is_set():
            try:
                await fn()
            except Exception:  # noqa: BLE001 - housekeeping must not die
                logger.exception("worker %s loop failed", name)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def _claim_loop(self) -> None:
        while not self._stopping.is_set():
            claimed = 0
            try:
                free = self._s.concurrency - len(self._running)
                if free > 0:
                    jobs = await self._repo.claim(queues=self._s.queues, worker_id=self._s.worker_id, lease_ttl=self._s.lease_ttl, limit=free)
                    for job in jobs:
                        await self._launch(job)
                    claimed = len(jobs)
            except Exception:  # noqa: BLE001
                logger.exception("claim failed")
            if claimed == 0:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._s.poll_interval)
                except TimeoutError:
                    pass

    async def serve(self) -> None:
        """Run until ``stop()``; then wait ``shutdown_grace`` for in-flight jobs and release the rest."""
        await self.announce()
        tasks = [
            asyncio.create_task(self._claim_loop()),
            asyncio.create_task(self._loop("reaper", self._s.reaper_interval, lambda: reap_expired_leases(self._repo))),
            asyncio.create_task(self._loop("scheduler", self._s.scheduler_interval, lambda: tick_schedules(self._repo))),
            asyncio.create_task(self._loop("heartbeat", self._s.worker_heartbeat_interval, self.announce)),
        ]
        await self._stopping.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._running:
            logger.info("waiting up to %.0fs for %d in-flight job(s)", self._s.shutdown_grace, len(self._running))
            done, pending = await asyncio.wait(list(self._running.values()), timeout=self._s.shutdown_grace)
            for t in pending:
                t.cancel()  # _run_job releases the lease on CancelledError
            await asyncio.gather(*pending, return_exceptions=True)
        await self.announce()

    def stop(self) -> None:
        self._stopping.set()


__all__ = ["Worker", "WorkerSettings", "reap_expired_leases"]
