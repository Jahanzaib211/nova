"""Jobs: the runner's queue, schedules and workers as operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, Ok, jobs_repo, section_enabled
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


class ListJobsIn(BaseModel):
    status: str | None = Field(default=None, description="queued | running | succeeded | failed | dead_letter | cancelled")
    type: str | None = Field(default=None, description="Job type, e.g. em.campaign.start or agents.task")
    limit: int = Field(default=50, ge=1, le=500)


class JobIdIn(BaseModel):
    job_id: str


class JobOut(BaseModel):
    job: dict[str, Any]


class EnqueueIn(BaseModel):
    type: str = Field(description="Registered job type")
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str = "default"
    run_after_seconds: int | None = Field(default=None, ge=0, description="Delay before the job becomes claimable")


class ScheduleIn(BaseModel):
    name: str
    type: str
    cron: str = Field(description="5-field cron expression")
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str = "default"
    enabled: bool = True
    timezone: str = "UTC"


class ScheduleIdIn(BaseModel):
    schedule_id: str


def _repo_or_raise():
    repo = jobs_repo()
    if repo is None:
        raise RuntimeError("jobs: no database configured")
    return repo


async def _list(ctx: OpContext, inp: ListJobsIn) -> Items:
    repo = _repo_or_raise()
    jobs = await repo.list_jobs(owner_user_id=None if ctx.is_admin else ctx.user_id, status=inp.status, job_type=inp.type, limit=inp.limit)
    return Items(items=jobs, total=len(jobs))


async def _owned(ctx: OpContext, job_id: str) -> dict[str, Any]:
    job = await _repo_or_raise().get(job_id)
    if job is None or (not ctx.is_admin and job.get("owner_user_id") not in (None, ctx.user_id)):
        raise LookupError(f"job {job_id} not found")
    return job


async def _get(ctx: OpContext, inp: JobIdIn) -> JobOut:
    return JobOut(job=await _owned(ctx, inp.job_id))


async def _events(ctx: OpContext, inp: JobIdIn) -> Items:
    await _owned(ctx, inp.job_id)
    events = await _repo_or_raise().list_events(inp.job_id, limit=500)
    return Items(items=events, total=len(events))


async def _cancel(ctx: OpContext, inp: JobIdIn) -> Ok:
    await _owned(ctx, inp.job_id)
    ok = await _repo_or_raise().request_cancel(inp.job_id)
    return Ok(ok=bool(ok), detail=None if ok else "job is not cancellable in its current state")


async def _retry(ctx: OpContext, inp: JobIdIn) -> Ok:
    await _owned(ctx, inp.job_id)
    ok = await _repo_or_raise().requeue(inp.job_id)
    return Ok(ok=bool(ok), detail=None if ok else "job is not in a retryable state")


async def _enqueue(ctx: OpContext, inp: EnqueueIn) -> JobOut:
    from datetime import UTC, datetime, timedelta

    from deerflow.jobs.queue import JobQueue

    repo = _repo_or_raise()
    run_after = datetime.now(UTC) + timedelta(seconds=inp.run_after_seconds) if inp.run_after_seconds else None
    job_id = await JobQueue(repo).enqueue(inp.type, inp.payload, queue=inp.queue, run_after=run_after, owner_user_id=ctx.user_id, thread_id=ctx.thread_id)
    return JobOut(job=await repo.get(job_id) or {"id": job_id})


async def _schedules(ctx: OpContext, inp: Empty) -> Items:
    rows = await _repo_or_raise().list_schedules(owner_user_id=None if ctx.is_admin else ctx.user_id)
    return Items(items=rows, total=len(rows))


async def _schedule_upsert(ctx: OpContext, inp: ScheduleIn) -> JobOut:
    from datetime import UTC, datetime

    from deerflow.jobs.scheduler import next_run_at

    repo = _repo_or_raise()
    first = next_run_at(inp.cron, datetime.now(UTC), timezone=inp.timezone)
    sid = await repo.upsert_schedule(name=inp.name, job_type=inp.type, cron=inp.cron, payload=inp.payload, queue=inp.queue, enabled=inp.enabled, timezone=inp.timezone, owner_user_id=ctx.user_id, next_run_at=first)
    return JobOut(job=await repo.get_schedule(sid) or {"id": sid})


async def _schedule_delete(ctx: OpContext, inp: ScheduleIdIn) -> Ok:
    repo = _repo_or_raise()
    sched = await repo.get_schedule(inp.schedule_id)
    if sched is None or (not ctx.is_admin and sched.get("owner_user_id") not in (None, ctx.user_id)):
        raise LookupError(f"schedule {inp.schedule_id} not found")
    return Ok(ok=await repo.delete_schedule(inp.schedule_id))


async def _workers(ctx: OpContext, inp: Empty) -> Items:
    rows = await _repo_or_raise().list_workers()
    return Items(items=rows, total=len(rows))


async def _summary(ctx: OpContext, inp: Empty) -> JobOut:
    return JobOut(job=await _repo_or_raise().summary())


async def _status() -> ModuleStatus:
    if not section_enabled("jobs"):
        return ModuleStatus(configured=False, healthy=False, detail="jobs.enabled is false")
    repo = jobs_repo()
    if repo is None:
        return ModuleStatus(configured=True, healthy=False, detail="no database")
    workers = await repo.list_workers()
    return ModuleStatus(configured=True, healthy=bool(workers), detail=f"{len(workers)} worker(s) heartbeating" if workers else "no worker heartbeat")


MODULE = CapabilityModule(
    id="jobs",
    title="Jobs",
    flag="jobs",
    config_key="jobs",
    description="Background job runner: queue, schedules (cron), workers.",
    status=_status,
    operations=[
        Operation(name="jobs.list", kind="read", input=ListJobsIn, output=Items, handler=_list, description="List the caller's jobs (all jobs for admins), newest first."),
        Operation(name="jobs.get", kind="read", input=JobIdIn, output=JobOut, handler=_get, description="One job with its current status, attempts and result."),
        Operation(name="jobs.events", kind="read", input=JobIdIn, output=Items, handler=_events, description="A job's event log (progress, logs, retries)."),
        Operation(name="jobs.enqueue", kind="execute", input=EnqueueIn, output=JobOut, handler=_enqueue, description="Enqueue a job of a registered type."),
        Operation(name="jobs.cancel", kind="write", input=JobIdIn, output=Ok, handler=_cancel, description="Request cooperative cancellation of a job."),
        Operation(name="jobs.retry", kind="write", input=JobIdIn, output=Ok, handler=_retry, description="Requeue a failed or dead-letter job."),
        Operation(name="jobs.schedules", kind="read", input=Empty, output=Items, handler=_schedules, description="List cron schedules."),
        Operation(name="jobs.schedule_upsert", kind="write", input=ScheduleIn, output=JobOut, handler=_schedule_upsert, description="Create or update a cron schedule by name."),
        Operation(name="jobs.schedule_delete", kind="write", input=ScheduleIdIn, output=Ok, handler=_schedule_delete, description="Delete a cron schedule."),
        Operation(name="jobs.workers", kind="read", input=Empty, output=Items, handler=_workers, description="Worker processes and their heartbeats (Cloud workers)."),
        Operation(name="jobs.summary", kind="read", input=Empty, output=JobOut, handler=_summary, description="Queue depth by status.", admin_only=True),
    ],
)
