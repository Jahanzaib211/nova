"""Job status and event vocabularies.

Single source of truth for the backend: the ``jobs`` table status column,
``job_events.type`` and the ``/api/jobs`` responses all use these enums.
The values are pinned to ``contracts/job_status_contract.json`` by
``tests/test_job_status_contract.py``; the frontend pins the same file.
"""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class JobEventType(str, Enum):
    ENQUEUED = "enqueued"
    LEASED = "leased"
    HEARTBEAT = "heartbeat"
    PROGRESS = "progress"
    LOG = "log"
    RETRY_SCHEDULED = "retry_scheduled"
    RELEASED = "released"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


JOB_STATUSES: tuple[str, ...] = tuple(s.value for s in JobStatus)
JOB_EVENT_TYPES: tuple[str, ...] = tuple(e.value for e in JobEventType)

# A job in one of these never changes status again; the reaper skips them
# and the UI stops polling.
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.DEAD_LETTER.value,
        JobStatus.CANCELLED.value,
    }
)


def is_terminal(status: JobStatus | str) -> bool:
    value = status.value if isinstance(status, JobStatus) else status
    return value in TERMINAL_JOB_STATUSES
