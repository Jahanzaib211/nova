"""What a running handler sees."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .errors import JobCancelled

if TYPE_CHECKING:
    from deerflow.persistence.job.sql import JobRepository


class JobContext:
    """Handle for one attempt of one job.

    ``heartbeat()`` is the cooperation point: it extends the lease so the
    reaper leaves the job alone, and raises ``JobCancelled`` once someone
    asked for the job to stop. Long handlers call it inside their loop; the
    worker also heartbeats automatically in the background so a handler that
    forgets still keeps its lease (it just cannot be cancelled mid-flight).
    """

    def __init__(self, repo: JobRepository, job: dict[str, Any], *, worker_id: str, lease_ttl: timedelta) -> None:
        self._repo = repo
        self._job = job
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl
        self.cancelled = False

    @property
    def job_id(self) -> str:
        return self._job["id"]

    @property
    def job_type(self) -> str:
        return self._job["type"]

    @property
    def payload(self) -> dict[str, Any]:
        return self._job["payload"]

    @property
    def attempt(self) -> int:
        return int(self._job["attempts"]) + 1

    @property
    def owner_user_id(self) -> str | None:
        return self._job.get("owner_user_id")

    @property
    def thread_id(self) -> str | None:
        return self._job.get("thread_id")

    async def heartbeat(self) -> None:
        if await self._repo.is_cancel_requested(self.job_id):
            self.cancelled = True
            raise JobCancelled(self.job_id)
        await self._repo.heartbeat(self.job_id, worker_id=self._worker_id, lease_ttl=self._lease_ttl)

    async def progress(self, pct: int, message: str | None = None, data: dict | None = None) -> None:
        await self._repo.progress(self.job_id, pct=pct, message=message, data=data)

    async def log(self, message: str) -> None:
        await self._repo.log(self.job_id, message)
