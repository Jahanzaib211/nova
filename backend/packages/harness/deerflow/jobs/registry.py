"""Job type → handler registry.

A handler is ``async def handler(ctx: JobContext) -> Any``. Its return value
is stored as the job's result; raising ``RetryableError`` schedules a retry,
``JobCancelled`` (from ``ctx.heartbeat()``) records a cancellation, anything
else fails the job.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .context import JobContext

JobHandler = Callable[[JobContext], Awaitable[Any]]


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise ValueError(f"job type already registered: {job_type}")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    def types(self) -> list[str]:
        return sorted(self._handlers)
