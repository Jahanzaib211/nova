"""Owner-checked job and schedule actions, shared by every surface.

``/api/jobs`` and the ``jobs.*`` capability operations both sit on
:class:`~deerflow.persistence.job.sql.JobRepository`. They used to apply
their own ownership and state rules, and drifted: the capability let any
caller overwrite another owner's schedule by name, cancel or retry system
jobs (``owner_user_id`` ``None``), and left queued jobs waiting on a worker
to notice a cancel. The rules live here once; each surface only maps
:class:`JobNotFound` / :class:`JobConflict` / ``ValueError`` to its own
error shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from deerflow.jobs.scheduler import next_run_at
from deerflow.jobs.status import JobStatus, is_terminal

#: Statuses a retry may start from; anything else is a conflict.
RETRYABLE_STATUSES: frozenset[str] = frozenset({JobStatus.FAILED.value, JobStatus.DEAD_LETTER.value})

#: Statuses no worker holds: a cancel settles these immediately.
_UNCLAIMED_STATUSES: frozenset[str] = frozenset({JobStatus.QUEUED.value, JobStatus.RETRYING.value})


class JobNotFound(LookupError):  # noqa: N818 - mirrors LookupError / KeyError naming
    """Missing, or owned by someone else (indistinguishable on purpose, so ids cannot be probed)."""


class JobConflict(Exception):  # noqa: N818 - surfaces map it to HTTP 409
    """The action does not fit the row's current state or owner."""


#: Job types a non-admin may enqueue or put on a cron. Each acts only on its
#: owner's own data (the email handlers scope by the job's owner). Everything
#: else — maintenance such as ``em.events.prune`` / ``em.bounce.poll`` and
#: internal fan-out such as ``em.campaign.batch`` — is admin-only on both
#: paths, so neither can be used to get around the other. Agents (harness and
#: MCP surfaces) never run as admin, so this is exactly what they may run.
USER_SCHEDULABLE_JOB_TYPES: frozenset[str] = frozenset(
    {
        "agents.task",
        "jobs.smoke.sleep",
        "em.campaign.start",
        "em.contacts.import",
        "em.twenty.sync",
        "em.twenty.import",
    }
)


def _visible(row: dict[str, Any], *, user_id: str, is_admin: bool) -> bool:
    # A system row (owner ``None``) belongs to nobody but the admins.
    return is_admin or (row.get("owner_user_id") is not None and row.get("owner_user_id") == user_id)


def first_run_at(cron: str, timezone: str) -> datetime:
    """The next fire time of ``cron`` from now; ``ValueError`` on a bad cron or timezone."""
    try:
        return next_run_at(cron, datetime.now(UTC), timezone=timezone)
    except KeyError as exc:  # unknown timezone
        raise ValueError(str(exc)) from exc


class JobService:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    # -- lookup -----------------------------------------------------------

    async def owned_job(self, job_id: str, *, user_id: str, is_admin: bool = False) -> dict[str, Any]:
        job = await self.repo.get(job_id)
        if job is None or not _visible(job, user_id=user_id, is_admin=is_admin):
            raise JobNotFound(f"job {job_id} not found")
        return job

    async def owned_schedule(self, schedule_id: str, *, user_id: str, is_admin: bool = False) -> dict[str, Any]:
        sched = await self.repo.get_schedule(schedule_id)
        if sched is None or not _visible(sched, user_id=user_id, is_admin=is_admin):
            raise JobNotFound(f"schedule {schedule_id} not found")
        return sched

    # -- job actions ------------------------------------------------------

    async def cancel(self, job: dict[str, Any]) -> str:
        """Cancel ``job`` (already owner-checked); returns the status it now reports.

        Queued and retrying jobs are settled at once — nothing is running
        them, so waiting for a worker would only delay the answer. A running
        job gets a cooperative cancel request and keeps its status until the
        worker acknowledges.
        """
        if is_terminal(job["status"]):
            raise JobConflict(f"job is already {job['status']}")
        await self.repo.request_cancel(job["id"])
        if job["status"] in _UNCLAIMED_STATUSES:
            await self.repo.mark_cancelled(job["id"])
            return JobStatus.CANCELLED.value
        return job["status"]

    async def retry(self, job: dict[str, Any]) -> bool:
        """Requeue a failed or dead-lettered ``job`` (already owner-checked)."""
        if job["status"] not in RETRYABLE_STATUSES:
            raise JobConflict(f"only failed or dead-lettered jobs can be retried (job is {job['status']})")
        return await self.repo.requeue(job["id"])

    # -- schedules --------------------------------------------------------

    async def upsert_schedule(self, *, user_id: str, name: str, job_type: str, cron: str, payload: dict[str, Any], queue: str = "default", enabled: bool = True, timezone: str = "UTC", is_admin: bool = False) -> dict[str, Any]:
        """Create or update the caller's schedule called ``name``.

        Names are global (the upsert matches by name), so a name held by
        another owner — or by the system — is a conflict, never a takeover.
        Non-admins may only schedule :data:`USER_SCHEDULABLE_JOB_TYPES`
        (``PermissionError`` otherwise).
        """
        first = first_run_at(cron, timezone)
        if not is_admin and job_type not in USER_SCHEDULABLE_JOB_TYPES:
            raise PermissionError(f"job type {job_type!r} can only be scheduled by an admin")
        existing = await self.repo.get_schedule_by_name(name)
        if existing is not None and existing.get("owner_user_id") != user_id:
            raise JobConflict("schedule name already in use")
        schedule_id = await self.repo.upsert_schedule(name=name, job_type=job_type, cron=cron, payload=payload, queue=queue, enabled=enabled, timezone=timezone, owner_user_id=user_id, next_run_at=first)
        sched = await self.repo.get_schedule(schedule_id)
        return sched if sched is not None else {"id": schedule_id}
