"""SQL repository for the job runner.

One class, one session per call, no ORM objects escape: every method returns
plain dicts so the worker, the gateway routers and tests share a shape.
Datetimes are tz-aware UTC on the way out (SQLite stores naive values).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.jobs.status import TERMINAL_JOB_STATUSES, JobEventType, JobStatus

from .model import JobEventRow, JobRow, JobScheduleRow, JobWorkerRow

_CLAIMABLE = (JobStatus.QUEUED.value, JobStatus.RETRYING.value)
_LEASED = (JobStatus.LEASED.value, JobStatus.RUNNING.value)
MAX_BACKOFF_SECONDS = 3600


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def backoff_delay(base_seconds: int, attempts: int) -> timedelta:
    """Exponential backoff with ±20 % jitter, capped at an hour."""
    raw = min(MAX_BACKOFF_SECONDS, base_seconds * (2 ** max(0, attempts - 1)))
    jitter = raw * 0.2 * (random.random() * 2 - 1)  # noqa: S311 - scheduling jitter, not security
    return timedelta(seconds=max(1.0, raw + jitter))


class JobRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── shaping ──────────────────────────────────────────────────────────
    @staticmethod
    def _job(row: JobRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "type": row.type,
            "queue": row.queue,
            "status": row.status,
            "priority": row.priority,
            "payload": row.payload_json or {},
            "result": row.result_json,
            "error": row.error,
            "owner_user_id": row.owner_user_id,
            "thread_id": row.thread_id,
            "dedupe_key": row.dedupe_key,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "backoff_seconds": row.backoff_seconds,
            "lease_owner": row.lease_owner,
            "lease_expires_at": _utc(row.lease_expires_at),
            "heartbeat_at": _utc(row.heartbeat_at),
            "run_after": _utc(row.run_after),
            "cancel_requested": bool(row.cancel_requested),
            "progress_pct": row.progress_pct,
            "progress_message": row.progress_message,
            "schedule_id": row.schedule_id,
            "created_at": _utc(row.created_at),
            "started_at": _utc(row.started_at),
            "finished_at": _utc(row.finished_at),
        }

    @staticmethod
    def _event(row: JobEventRow) -> dict[str, Any]:
        return {"id": row.id, "job_id": row.job_id, "seq": row.seq, "type": row.type, "payload": row.payload_json or {}, "created_at": _utc(row.created_at)}

    @staticmethod
    def _schedule(row: JobScheduleRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "queue": row.queue,
            "cron": row.cron,
            "timezone": row.timezone,
            "payload": row.payload_json or {},
            "enabled": bool(row.enabled),
            "owner_user_id": row.owner_user_id,
            "last_enqueued_for": _utc(row.last_enqueued_for),
            "next_run_at": _utc(row.next_run_at),
            "created_at": _utc(row.created_at),
        }

    async def _append_event(self, session: AsyncSession, job_id: str, type_: JobEventType, payload: dict | None = None) -> None:
        seq = (await session.execute(select(func.coalesce(func.max(JobEventRow.seq), 0)).where(JobEventRow.job_id == job_id))).scalar_one()
        session.add(JobEventRow(job_id=job_id, seq=int(seq) + 1, type=type_.value, payload_json=payload or {}))

    # ── enqueue / read ───────────────────────────────────────────────────
    async def enqueue(
        self,
        job_type: str,
        payload: dict,
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
        async with self._sf() as session:
            if dedupe_key is not None:
                live = (await session.execute(select(JobRow.id).where(JobRow.dedupe_key == dedupe_key, JobRow.status.not_in(tuple(TERMINAL_JOB_STATUSES))))).scalars().first()
                if live is not None:
                    return live
            job_id = str(uuid.uuid4())
            session.add(
                JobRow(
                    id=job_id,
                    type=job_type,
                    queue=queue,
                    priority=priority,
                    payload_json=payload,
                    run_after=run_after or datetime.now(UTC),
                    dedupe_key=dedupe_key,
                    owner_user_id=owner_user_id,
                    thread_id=thread_id,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                    schedule_id=schedule_id,
                )
            )
            await self._append_event(session, job_id, JobEventType.ENQUEUED, {"type": job_type, "queue": queue})
            await session.commit()
            return job_id

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(JobRow, job_id)
            return self._job(row) if row else None

    async def list_jobs(self, *, owner_user_id: str | None = None, status: str | None = None, job_type: str | None = None, queue: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        stmt = select(JobRow)
        if owner_user_id is not None:
            stmt = stmt.where(JobRow.owner_user_id == owner_user_id)
        if status is not None:
            stmt = stmt.where(JobRow.status == status)
        if job_type is not None:
            stmt = stmt.where(JobRow.type == job_type)
        if queue is not None:
            stmt = stmt.where(JobRow.queue == queue)
        stmt = stmt.order_by(JobRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            return [self._job(r) for r in (await session.execute(stmt)).scalars().all()]

    async def list_events(self, job_id: str, *, since_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (await session.execute(select(JobEventRow).where(JobEventRow.job_id == job_id, JobEventRow.seq > since_seq).order_by(JobEventRow.seq).limit(limit))).scalars().all()
            return [self._event(r) for r in rows]

    async def summary(self) -> dict[str, Any]:
        async with self._sf() as session:
            counts = dict((await session.execute(select(JobRow.status, func.count()).group_by(JobRow.status))).all())
            oldest = (await session.execute(select(func.min(JobRow.run_after)).where(JobRow.status == JobStatus.QUEUED.value))).scalar_one()
        oldest_dt = _utc(oldest)
        return {
            "queued": int(counts.get(JobStatus.QUEUED.value, 0)),
            "leased": int(counts.get(JobStatus.LEASED.value, 0)),
            "running": int(counts.get(JobStatus.RUNNING.value, 0)),
            "retrying": int(counts.get(JobStatus.RETRYING.value, 0)),
            "dead_letter": int(counts.get(JobStatus.DEAD_LETTER.value, 0)),
            "oldest_queued_age_s": max(0.0, (datetime.now(UTC) - oldest_dt).total_seconds()) if oldest_dt else None,
        }

    # ── lease lifecycle ──────────────────────────────────────────────────
    async def claim(self, *, queues: list[str], worker_id: str, lease_ttl: timedelta, limit: int = 1, now: datetime | None = None) -> list[dict[str, Any]]:
        """Lease up to ``limit`` due jobs. ``FOR UPDATE SKIP LOCKED`` on Postgres;
        SQLite ignores the clause and serialises writers instead."""
        now = now or datetime.now(UTC)
        claimed: list[dict[str, Any]] = []
        async with self._sf() as session:
            stmt = select(JobRow).where(JobRow.status.in_(_CLAIMABLE), JobRow.queue.in_(queues), JobRow.run_after <= now).order_by(JobRow.priority.desc(), JobRow.run_after).limit(limit).with_for_update(skip_locked=True)
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                res = await session.execute(update(JobRow).where(JobRow.id == row.id, JobRow.status.in_(_CLAIMABLE)).values(status=JobStatus.LEASED.value, lease_owner=worker_id, lease_expires_at=now + lease_ttl, heartbeat_at=now))
                if res.rowcount != 1:
                    continue  # lost the race on SQLite; the other claimer has it
                await self._append_event(session, row.id, JobEventType.LEASED, {"worker_id": worker_id})
                await session.refresh(row)
                claimed.append(self._job(row))
            await session.commit()
        return claimed

    async def mark_running(self, job_id: str, *, worker_id: str) -> None:
        now = datetime.now(UTC)
        async with self._sf() as session:
            await session.execute(update(JobRow).where(JobRow.id == job_id, JobRow.lease_owner == worker_id).values(status=JobStatus.RUNNING.value, started_at=now, heartbeat_at=now))
            await session.commit()

    async def heartbeat(self, job_id: str, *, worker_id: str, lease_ttl: timedelta) -> bool:
        now = datetime.now(UTC)
        async with self._sf() as session:
            res = await session.execute(update(JobRow).where(JobRow.id == job_id, JobRow.lease_owner == worker_id, JobRow.status.in_(_LEASED)).values(lease_expires_at=now + lease_ttl, heartbeat_at=now))
            await session.commit()
            return res.rowcount == 1

    async def progress(self, job_id: str, *, pct: int, message: str | None = None, data: dict | None = None) -> None:
        pct = max(0, min(100, int(pct)))
        async with self._sf() as session:
            await session.execute(update(JobRow).where(JobRow.id == job_id).values(progress_pct=pct, progress_message=message))
            await self._append_event(session, job_id, JobEventType.PROGRESS, {"pct": pct, "message": message, "data": data})
            await session.commit()

    async def log(self, job_id: str, message: str) -> None:
        async with self._sf() as session:
            await self._append_event(session, job_id, JobEventType.LOG, {"message": message})
            await session.commit()

    async def request_cancel(self, job_id: str) -> bool:
        async with self._sf() as session:
            res = await session.execute(update(JobRow).where(JobRow.id == job_id, JobRow.status.not_in(tuple(TERMINAL_JOB_STATUSES))).values(cancel_requested=True))
            if res.rowcount == 1:
                await self._append_event(session, job_id, JobEventType.CANCEL_REQUESTED)
            await session.commit()
            return res.rowcount == 1

    async def is_cancel_requested(self, job_id: str) -> bool:
        async with self._sf() as session:
            return bool((await session.execute(select(JobRow.cancel_requested).where(JobRow.id == job_id))).scalar_one_or_none())

    async def _finish(self, session: AsyncSession, job_id: str, status: JobStatus, **values: Any) -> None:
        await session.execute(update(JobRow).where(JobRow.id == job_id).values(status=status.value, finished_at=datetime.now(UTC), lease_owner=None, lease_expires_at=None, **values))

    async def mark_succeeded(self, job_id: str, *, result: Any = None) -> None:
        async with self._sf() as session:
            await self._finish(session, job_id, JobStatus.SUCCEEDED, result_json=result, progress_pct=100)
            await self._append_event(session, job_id, JobEventType.SUCCEEDED)
            await session.commit()

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        async with self._sf() as session:
            await self._finish(session, job_id, JobStatus.FAILED, error=error[:4000])
            await self._append_event(session, job_id, JobEventType.FAILED, {"error": error[:1000]})
            await session.commit()

    async def mark_dead_letter(self, job_id: str, *, error: str) -> None:
        async with self._sf() as session:
            await self._finish(session, job_id, JobStatus.DEAD_LETTER, error=error[:4000])
            await self._append_event(session, job_id, JobEventType.DEAD_LETTERED, {"error": error[:1000]})
            await session.commit()

    async def mark_cancelled(self, job_id: str) -> None:
        async with self._sf() as session:
            await self._finish(session, job_id, JobStatus.CANCELLED)
            await self._append_event(session, job_id, JobEventType.CANCELLED)
            await session.commit()

    async def schedule_retry(self, job_id: str, *, error: str, backoff_seconds: int | None = None, delay: float | None = None) -> str:
        """Count the attempt; retry with backoff or dead-letter when exhausted.
        Returns the resulting status value."""
        async with self._sf() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                return JobStatus.FAILED.value
            attempts = row.attempts + 1
            if attempts >= row.max_attempts:
                await self._finish(session, job_id, JobStatus.DEAD_LETTER, attempts=attempts, error=error[:4000])
                await self._append_event(session, job_id, JobEventType.DEAD_LETTERED, {"error": error[:1000], "attempts": attempts})
                await session.commit()
                return JobStatus.DEAD_LETTER.value
            wait = timedelta(seconds=delay) if delay is not None else backoff_delay(backoff_seconds or row.backoff_seconds, attempts)
            run_after = datetime.now(UTC) + wait
            await session.execute(update(JobRow).where(JobRow.id == job_id).values(status=JobStatus.RETRYING.value, attempts=attempts, error=error[:4000], run_after=run_after, lease_owner=None, lease_expires_at=None))
            await self._append_event(session, job_id, JobEventType.RETRY_SCHEDULED, {"error": error[:1000], "attempts": attempts, "run_after": run_after.isoformat()})
            await session.commit()
            return JobStatus.RETRYING.value

    async def release(self, job_id: str, *, worker_id: str) -> None:
        """Hand a leased job back (worker shutting down)."""
        async with self._sf() as session:
            res = await session.execute(update(JobRow).where(JobRow.id == job_id, JobRow.lease_owner == worker_id, JobRow.status.in_(_LEASED)).values(status=JobStatus.QUEUED.value, lease_owner=None, lease_expires_at=None))
            if res.rowcount == 1:
                await self._append_event(session, job_id, JobEventType.RELEASED, {"worker_id": worker_id})
            await session.commit()

    async def expired_leases(self, *, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        async with self._sf() as session:
            return list((await session.execute(select(JobRow.id).where(JobRow.status.in_(_LEASED), JobRow.lease_expires_at.is_not(None), JobRow.lease_expires_at < now))).scalars().all())

    # ── schedules ────────────────────────────────────────────────────────
    async def upsert_schedule(self, *, name: str, job_type: str, cron: str, payload: dict, queue: str = "default", enabled: bool = True, timezone: str = "UTC", owner_user_id: str | None = None, next_run_at: datetime | None = None) -> str:
        async with self._sf() as session:
            row = (await session.execute(select(JobScheduleRow).where(JobScheduleRow.name == name))).scalars().first()
            if row is None:
                row = JobScheduleRow(id=str(uuid.uuid4()), name=name)
                session.add(row)
            row.type, row.cron, row.payload_json, row.queue, row.enabled, row.timezone, row.owner_user_id = job_type, cron, payload, queue, enabled, timezone, owner_user_id
            if next_run_at is not None:
                row.next_run_at = next_run_at
            await session.commit()
            return row.id

    async def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(JobScheduleRow, schedule_id)
            return self._schedule(row) if row else None

    async def get_schedule_by_name(self, name: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(select(JobScheduleRow).where(JobScheduleRow.name == name))).scalars().first()
            return self._schedule(row) if row else None

    async def list_schedules(self, *, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(JobScheduleRow).order_by(JobScheduleRow.name)
        if owner_user_id is not None:
            stmt = stmt.where(JobScheduleRow.owner_user_id == owner_user_id)
        async with self._sf() as session:
            return [self._schedule(r) for r in (await session.execute(stmt)).scalars().all()]

    async def due_schedules(self, *, now: datetime) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (await session.execute(select(JobScheduleRow).where(JobScheduleRow.enabled.is_(True), or_(JobScheduleRow.next_run_at.is_(None), JobScheduleRow.next_run_at <= now)))).scalars().all()
            return [self._schedule(r) for r in rows]

    async def advance_schedule(self, schedule_id: str, *, enqueued_for: datetime, next_run_at: datetime) -> None:
        async with self._sf() as session:
            await session.execute(update(JobScheduleRow).where(JobScheduleRow.id == schedule_id).values(last_enqueued_for=enqueued_for, next_run_at=next_run_at))
            await session.commit()

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self._sf() as session:
            row = await session.get(JobScheduleRow, schedule_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ── workers ──────────────────────────────────────────────────────────
    async def upsert_worker(self, *, worker_id: str, hostname: str, pid: int, queues: list[str], version: str, current_job_ids: list[str]) -> None:
        async with self._sf() as session:
            row = await session.get(JobWorkerRow, worker_id)
            if row is None:
                row = JobWorkerRow(worker_id=worker_id)
                session.add(row)
            row.hostname, row.pid, row.queues_json, row.version, row.current_job_ids_json = hostname, pid, queues, version, current_job_ids
            row.last_seen_at = datetime.now(UTC)
            await session.commit()

    async def list_workers(self) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (await session.execute(select(JobWorkerRow).order_by(JobWorkerRow.worker_id))).scalars().all()
            return [
                {
                    "worker_id": r.worker_id,
                    "hostname": r.hostname,
                    "pid": r.pid,
                    "queues": r.queues_json or [],
                    "version": r.version,
                    "started_at": _utc(r.started_at),
                    "last_seen_at": _utc(r.last_seen_at),
                    "current_job_ids": r.current_job_ids_json or [],
                }
                for r in rows
            ]

    async def prune_events(self, *, older_than: datetime) -> int:
        from sqlalchemy import delete

        async with self._sf() as session:
            res = await session.execute(delete(JobEventRow).where(JobEventRow.created_at < older_than, JobEventRow.job_id.in_(select(JobRow.id).where(JobRow.status.in_(tuple(TERMINAL_JOB_STATUSES))))))
            await session.commit()
            return int(res.rowcount or 0)


__all__ = ["JobRepository", "backoff_delay"]
