"""Producer-side facade: enqueue jobs and request cancellation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deerflow.persistence.job.sql import JobRepository


class JobQueue:
    def __init__(self, repo: JobRepository) -> None:
        self._repo = repo

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        queue: str = "default",
        priority: int = 0,
        run_after: datetime | None = None,
        dedupe_key: str | None = None,
        owner_user_id: str | None = None,
        thread_id: str | None = None,
        max_attempts: int = 5,
        backoff_seconds: int = 30,
        schedule_id: str | None = None,
    ) -> str:
        """Return the job id — the existing live job's id when ``dedupe_key`` matches."""
        return await self._repo.enqueue(
            job_type,
            payload or {},
            queue=queue,
            priority=priority,
            run_after=run_after,
            dedupe_key=dedupe_key,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            schedule_id=schedule_id,
        )

    async def cancel(self, job_id: str) -> bool:
        return await self._repo.request_cancel(job_id)
