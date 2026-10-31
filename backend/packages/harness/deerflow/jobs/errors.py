"""Exceptions a job handler raises to steer the runner."""

from __future__ import annotations


class RetryableError(Exception):
    """The attempt failed for a transient reason; try again later.

    ``delay`` overrides the job's exponential backoff for this retry.
    """

    def __init__(self, message: str = "retryable failure", *, delay: float | None = None) -> None:
        super().__init__(message)
        self.delay = delay


class JobCancelled(Exception):  # noqa: N818 - control-flow signal, mirrors asyncio.CancelledError
    """Raised by ``JobContext.heartbeat()`` once cancellation was requested."""
