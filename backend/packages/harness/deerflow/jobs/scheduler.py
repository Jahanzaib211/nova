"""Cron schedules → enqueued jobs.

``tick_schedules`` is called by the worker every ``scheduler_interval``: every
enabled schedule whose ``next_run_at`` has passed gets exactly one job (the
dedupe key ``sched:<id>:<iso>`` makes a second worker's tick a no-op), then
``next_run_at`` advances from *now* — a schedule that missed many windows
while the worker was down catches up once, not once per missed window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from croniter import croniter

if TYPE_CHECKING:
    from deerflow.persistence.job.sql import JobRepository


def next_run_at(cron: str, after: datetime, *, timezone: str = "UTC") -> datetime:
    """Next fire time strictly after ``after`` (tz-aware, returned in UTC)."""
    if not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression: {cron!r}")
    tz = ZoneInfo(timezone)
    local = after.astimezone(tz)
    nxt = croniter(cron, local).get_next(datetime)
    return nxt.astimezone(UTC)


async def tick_schedules(repo: JobRepository, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    enqueued: list[dict[str, Any]] = []
    for sched in await repo.due_schedules(now=now):
        scheduled_for = sched["next_run_at"] or now
        job_id = await repo.enqueue(
            sched["type"],
            sched["payload"],
            queue=sched["queue"],
            dedupe_key=f"sched:{sched['id']}:{scheduled_for.isoformat()}",
            owner_user_id=sched["owner_user_id"],
            schedule_id=sched["id"],
        )
        await repo.advance_schedule(sched["id"], enqueued_for=scheduled_for, next_run_at=next_run_at(sched["cron"], now, timezone=sched["timezone"]))
        enqueued.append({"schedule_id": sched["id"], "job_id": job_id, "scheduled_for": scheduled_for})
    return enqueued
