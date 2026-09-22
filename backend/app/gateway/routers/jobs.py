"""User-facing job runner API: ``/api/jobs``.

Owner-scoped: a user sees and controls only jobs and schedules with their
``owner_user_id``. The worker process runs the jobs; this router enqueues,
reads, cancels and re-queues. Progress is exposed two ways — a JSON poll
(``/{id}/events?since_seq=``) and an SSE stream that polls ``job_events`` on
the caller's behalf until the job is terminal. Statuses are the contract in
``contracts/job_status_contract.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.gateway.authz import AuthContext, require_auth
from app.gateway.deps import get_jobs_repo
from deerflow.jobs.scheduler import next_run_at
from deerflow.jobs.status import JOB_STATUSES, JobStatus, is_terminal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

EVENT_POLL_SECONDS = 0.75
STREAM_MAX_SECONDS = 6 * 3600


# ── schemas ─────────────────────────────────────────────────────────────


class JobResponse(BaseModel):
    id: str
    type: str
    queue: str
    status: str
    priority: int
    payload: dict[str, Any]
    result: Any = None
    error: str | None = None
    thread_id: str | None = None
    attempts: int
    max_attempts: int
    progress_pct: int
    progress_message: str | None = None
    cancel_requested: bool
    schedule_id: str | None = None
    run_after: datetime
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    limit: int
    offset: int


class JobEventResponse(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class JobEventsResponse(BaseModel):
    job_id: str
    status: str
    events: list[JobEventResponse]


class ActionResponse(BaseModel):
    ok: bool
    status: str


class ScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    cron: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str = Field(default="default", max_length=32)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    cron: str | None = Field(default=None, min_length=1, max_length=128)
    payload: dict[str, Any] | None = None
    queue: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class ScheduleResponse(BaseModel):
    id: str
    name: str
    type: str
    queue: str
    cron: str
    timezone: str
    payload: dict[str, Any]
    enabled: bool
    last_enqueued_for: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]


# ── helpers ─────────────────────────────────────────────────────────────


def _user_id(request: Request) -> str:
    auth: AuthContext = request.state.auth
    if auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _to_job(job: dict[str, Any]) -> JobResponse:
    return JobResponse(**{k: job[k] for k in JobResponse.model_fields})


def _to_schedule(s: dict[str, Any]) -> ScheduleResponse:
    return ScheduleResponse(**{k: s[k] for k in ScheduleResponse.model_fields})


async def _owned_job(request: Request, job_id: str) -> dict[str, Any]:
    """The job, or 404 — a job that belongs to someone else is also 404 so
    ids cannot be probed."""
    repo = get_jobs_repo(request)
    job = await repo.get(job_id)
    if job is None or job["owner_user_id"] != _user_id(request):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _owned_schedule(request: Request, schedule_id: str) -> dict[str, Any]:
    repo = get_jobs_repo(request)
    sched = await repo.get_schedule(schedule_id)
    if sched is None or sched["owner_user_id"] != _user_id(request):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sched


# ── schedules (declared before /{job_id} so the literal path wins) ───────────────────────────────────────────────────────────


@router.get("/schedules", response_model=ScheduleListResponse)
@require_auth
async def list_schedules(request: Request) -> ScheduleListResponse:
    scheds = await get_jobs_repo(request).list_schedules(owner_user_id=_user_id(request))
    return ScheduleListResponse(schedules=[_to_schedule(s) for s in scheds])


@router.post("/schedules", response_model=ScheduleResponse, status_code=201)
@require_auth
async def create_schedule(body: ScheduleCreateRequest, request: Request) -> ScheduleResponse:
    repo = get_jobs_repo(request)
    user_id = _user_id(request)
    try:
        first = next_run_at(body.cron, datetime.now(UTC), timezone=body.timezone)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = await repo.get_schedule_by_name(body.name)
    if existing is not None and existing["owner_user_id"] != user_id:
        raise HTTPException(status_code=409, detail="schedule name already in use")
    schedule_id = await repo.upsert_schedule(name=body.name, job_type=body.type, cron=body.cron, payload=body.payload, queue=body.queue, enabled=body.enabled, timezone=body.timezone, owner_user_id=user_id, next_run_at=first)
    sched = await repo.get_schedule(schedule_id)
    assert sched is not None
    return _to_schedule(sched)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
@require_auth
async def update_schedule(schedule_id: str, body: ScheduleUpdateRequest, request: Request) -> ScheduleResponse:
    sched = await _owned_schedule(request, schedule_id)
    repo = get_jobs_repo(request)
    cron = body.cron or sched["cron"]
    timezone = body.timezone or sched["timezone"]
    try:
        first = next_run_at(cron, datetime.now(UTC), timezone=timezone)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_schedule(
        name=sched["name"],
        job_type=sched["type"],
        cron=cron,
        payload=body.payload if body.payload is not None else sched["payload"],
        queue=body.queue or sched["queue"],
        enabled=sched["enabled"] if body.enabled is None else body.enabled,
        timezone=timezone,
        owner_user_id=sched["owner_user_id"],
        next_run_at=first if (body.cron or body.timezone) else None,
    )
    updated = await repo.get_schedule(schedule_id)
    assert updated is not None
    return _to_schedule(updated)


@router.delete("/schedules/{schedule_id}", response_model=ActionResponse)
@require_auth
async def delete_schedule(schedule_id: str, request: Request) -> ActionResponse:
    await _owned_schedule(request, schedule_id)
    ok = await get_jobs_repo(request).delete_schedule(schedule_id)
    return ActionResponse(ok=ok, status="deleted" if ok else "missing")


# ── jobs ────────────────────────────────────────────────────────────────


@router.get("", response_model=JobListResponse)
@require_auth
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),  # noqa: A002 - query name mirrors the field
    queue: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    if status is not None and status not in JOB_STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status {status!r}")
    repo = get_jobs_repo(request)
    jobs = await repo.list_jobs(owner_user_id=_user_id(request), status=status, job_type=type, queue=queue, limit=limit, offset=offset)
    return JobListResponse(jobs=[_to_job(j) for j in jobs], limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse)
@require_auth
async def get_job(job_id: str, request: Request) -> JobResponse:
    return _to_job(await _owned_job(request, job_id))


@router.get("/{job_id}/events", response_model=JobEventsResponse)
@require_auth
async def list_job_events(job_id: str, request: Request, since_seq: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=1000)) -> JobEventsResponse:
    job = await _owned_job(request, job_id)
    events = await get_jobs_repo(request).list_events(job_id, since_seq=since_seq, limit=limit)
    return JobEventsResponse(job_id=job_id, status=job["status"], events=[JobEventResponse(**{k: e[k] for k in JobEventResponse.model_fields}) for e in events])


@router.get("/{job_id}/events/stream")
@require_auth
async def stream_job_events(job_id: str, request: Request, since_seq: int = Query(default=0, ge=0)) -> StreamingResponse:
    """SSE: every job event as a named ``job_event`` frame, then a final
    ``job_done`` frame once the job is terminal. Polls ``job_events``; a
    Postgres LISTEN fast path can replace the poll without changing the wire."""
    await _owned_job(request, job_id)
    repo = get_jobs_repo(request)

    async def gen():
        seq = since_seq
        deadline = asyncio.get_running_loop().time() + STREAM_MAX_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if await request.is_disconnected():
                return
            for event in await repo.list_events(job_id, since_seq=seq):
                seq = event["seq"]
                yield f"event: job_event\ndata: {json.dumps({'seq': event['seq'], 'type': event['type'], 'payload': event['payload'], 'created_at': event['created_at'].isoformat()})}\n\n"
            job = await repo.get(job_id)
            if job is None or is_terminal(job["status"]):
                yield f"event: job_done\ndata: {json.dumps({'status': job['status'] if job else 'failed'})}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(EVENT_POLL_SECONDS)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.post("/{job_id}/cancel", response_model=ActionResponse)
@require_auth
async def cancel_job(job_id: str, request: Request) -> ActionResponse:
    job = await _owned_job(request, job_id)
    if is_terminal(job["status"]):
        raise HTTPException(status_code=409, detail=f"job is already {job['status']}")
    repo = get_jobs_repo(request)
    if job["status"] in (JobStatus.QUEUED.value, JobStatus.RETRYING.value):
        # Nothing is running it: settle it now rather than waiting for a worker.
        await repo.request_cancel(job_id)
        await repo.mark_cancelled(job_id)
        return ActionResponse(ok=True, status=JobStatus.CANCELLED.value)
    await repo.request_cancel(job_id)
    return ActionResponse(ok=True, status=job["status"])


@router.post("/{job_id}/retry", response_model=ActionResponse)
@require_auth
async def retry_job(job_id: str, request: Request) -> ActionResponse:
    job = await _owned_job(request, job_id)
    if job["status"] not in (JobStatus.FAILED.value, JobStatus.DEAD_LETTER.value):
        raise HTTPException(status_code=409, detail=f"only failed or dead-lettered jobs can be retried (job is {job['status']})")
    ok = await get_jobs_repo(request).requeue(job_id)
    return ActionResponse(ok=ok, status=JobStatus.QUEUED.value if ok else job["status"])
