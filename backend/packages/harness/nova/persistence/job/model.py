"""Job runner tables: jobs, job_events, job_schedules, job_workers.

Statuses and event types are pinned to ``contracts/job_status_contract.json``
(see ``deerflow.jobs.status``). Rows are owner-scoped like ``runs``
(``owner_user_id``), and a job may belong to a thread so its progress can be
mirrored into that thread's Agent's Computer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, default="default", index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Unique while the job is non-terminal; enforced in JobRepository.enqueue.
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The claim query: status IN (queued, retrying) AND queue = ? ORDER BY priority DESC, run_after.
        Index("ix_jobs_claim", "status", "queue", "priority", "run_after"),
    )


class JobEventRow(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_job_events_job_seq", "job_id", "seq"),)


class JobScheduleRow(Base):
    __tablename__ = "job_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    cron: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_enqueued_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class JobWorkerRow(Base):
    __tablename__ = "job_workers"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queues_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    current_job_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
