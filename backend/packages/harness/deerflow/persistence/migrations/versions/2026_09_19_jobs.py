"""Job runner tables: jobs, job_events, job_schedules, job_workers.

Revision ID: 2026_09_19_jobs
Revises: 2026_08_13_password_reset_tokens

Idempotent like every version here: create_all at boot creates these on a
fresh database, so the migration only acts on a database that predates the
tables (see backend/tests/test_migrations_apply_clean.py).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2026_09_19_jobs"
down_revision: str | None = "2026_08_13_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "jobs" not in existing:
        op.create_table(
            "jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("queue", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("owner_user_id", sa.String(length=64), nullable=True),
            sa.Column("thread_id", sa.String(length=64), nullable=True),
            sa.Column("dedupe_key", sa.String(length=128), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("backoff_seconds", sa.Integer(), nullable=False),
            sa.Column("lease_owner", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("progress_pct", sa.Integer(), nullable=False),
            sa.Column("progress_message", sa.Text(), nullable=True),
            sa.Column("schedule_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        for name, cols in (
            ("ix_jobs_type", ["type"]),
            ("ix_jobs_queue", ["queue"]),
            ("ix_jobs_status", ["status"]),
            ("ix_jobs_owner_user_id", ["owner_user_id"]),
            ("ix_jobs_thread_id", ["thread_id"]),
            ("ix_jobs_dedupe_key", ["dedupe_key"]),
            ("ix_jobs_lease_expires_at", ["lease_expires_at"]),
            ("ix_jobs_run_after", ["run_after"]),
            ("ix_jobs_schedule_id", ["schedule_id"]),
            ("ix_jobs_claim", ["status", "queue", "priority", "run_after"]),
        ):
            op.create_index(name, "jobs", cols)

    if "job_events" not in existing:
        op.create_table(
            "job_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(length=36), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
        op.create_index("ix_job_events_job_seq", "job_events", ["job_id", "seq"])

    if "job_schedules" not in existing:
        op.create_table(
            "job_schedules",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("queue", sa.String(length=32), nullable=False),
            sa.Column("cron", sa.String(length=128), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=True),
            sa.Column("last_enqueued_for", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_job_schedules_owner_user_id", "job_schedules", ["owner_user_id"])
        op.create_index("ix_job_schedules_next_run_at", "job_schedules", ["next_run_at"])

    if "job_workers" not in existing:
        op.create_table(
            "job_workers",
            sa.Column("worker_id", sa.String(length=64), primary_key=True),
            sa.Column("hostname", sa.String(length=128), nullable=False),
            sa.Column("pid", sa.Integer(), nullable=False),
            sa.Column("queues_json", sa.JSON(), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("current_job_ids_json", sa.JSON(), nullable=False),
        )


def downgrade() -> None:
    for table in ("job_workers", "job_schedules", "job_events", "jobs"):
        op.drop_table(table)
