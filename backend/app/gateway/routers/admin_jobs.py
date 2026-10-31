"""Operator view of the job runner: ``/api/v1/admin/jobs``.

Admin-only (``X-Nova-Ops-Token`` from the ops console, or an admin session).
Everything the watchdog and nova-ops need: a summary with worker liveness,
the dead-letter queue, cross-user listing, cancel/retry of any job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway.deps import get_jobs_repo, require_admin_user
from app.gateway.routers.jobs import ActionResponse, JobListResponse, JobStatus, _to_job, is_terminal

router = APIRouter(prefix="/api/v1/admin/jobs", tags=["admin", "jobs"])

_ADMIN_DETAIL = "Admin privileges required to operate the job runner."


class WorkerResponse(BaseModel):
    worker_id: str
    hostname: str
    pid: int
    queues: list[str]
    version: str
    started_at: datetime
    last_seen_at: datetime
    last_seen_age_s: float
    current_job_ids: list[str]


class JobsSummaryResponse(BaseModel):
    queued: int
    leased: int
    running: int
    retrying: int
    dead_letter: int
    oldest_queued_age_s: float | None
    workers: list[WorkerResponse]
    checked_at: datetime


def _worker(w: dict[str, Any], now: datetime) -> WorkerResponse:
    return WorkerResponse(**w, last_seen_age_s=max(0.0, (now - w["last_seen_at"]).total_seconds()))


@router.get("/summary", response_model=JobsSummaryResponse)
async def jobs_summary(request: Request) -> JobsSummaryResponse:
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    repo = get_jobs_repo(request)
    now = datetime.now(UTC)
    summary = await repo.summary()
    workers = [_worker(w, now) for w in await repo.list_workers()]
    return JobsSummaryResponse(**summary, workers=workers, checked_at=now)


@router.get("/workers", response_model=list[WorkerResponse])
async def list_workers(request: Request) -> list[WorkerResponse]:
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    now = datetime.now(UTC)
    return [_worker(w, now) for w in await get_jobs_repo(request).list_workers()]


@router.get("", response_model=JobListResponse)
async def list_all_jobs(
    request: Request, status: str | None = Query(default=None), type: str | None = Query(default=None), queue: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)
) -> JobListResponse:  # noqa: A002
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    jobs = await get_jobs_repo(request).list_jobs(status=status, job_type=type, queue=queue, limit=limit, offset=offset)
    return JobListResponse(jobs=[_to_job(j) for j in jobs], limit=limit, offset=offset)


@router.get("/dead-letter", response_model=JobListResponse)
async def dead_letter(request: Request, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> JobListResponse:
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    jobs = await get_jobs_repo(request).list_jobs(status=JobStatus.DEAD_LETTER.value, limit=limit, offset=offset)
    return JobListResponse(jobs=[_to_job(j) for j in jobs], limit=limit, offset=offset)


@router.post("/{job_id}/cancel", response_model=ActionResponse)
async def admin_cancel(job_id: str, request: Request) -> ActionResponse:
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    repo = get_jobs_repo(request)
    job = await repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if is_terminal(job["status"]):
        raise HTTPException(status_code=409, detail=f"job is already {job['status']}")
    await repo.request_cancel(job_id)
    if job["status"] in (JobStatus.QUEUED.value, JobStatus.RETRYING.value):
        await repo.mark_cancelled(job_id)
        return ActionResponse(ok=True, status=JobStatus.CANCELLED.value)
    return ActionResponse(ok=True, status=job["status"])


@router.post("/{job_id}/retry", response_model=ActionResponse)
async def admin_retry(job_id: str, request: Request) -> ActionResponse:
    await require_admin_user(request, detail=_ADMIN_DETAIL)
    repo = get_jobs_repo(request)
    job = await repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    ok = await repo.requeue(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"only failed or dead-lettered jobs can be retried (job is {job['status']})")
    return ActionResponse(ok=True, status=JobStatus.QUEUED.value)
