"""ops-console controls: user limit override/reset, admin_audit, runs indexes

Phase C9.4 — data plane for the Ali Technologies ops console:

- ``users.daily_limit_override`` — custom daily token allowance that wins
  over the plan limit when set.
- ``users.credit_usage_reset_at`` — operator usage-reset marker; the credit
  meter counts runs only after ``max(utc_midnight, reset_at)``.
- ``admin_audit`` table — append-only trail of operator actions.
- ``runs`` indexes — ``(user_id, created_at)`` for the per-request credit
  meter and ``created_at`` for the cross-user activity feed.

Idempotent throughout (column/table/index existence checked first), matching
the other 2026_07_1x migrations.

Revision ID: 2026_07_16_ops_controls
Revises: 2026_07_15_user_api_keys
Create Date: 2026-07-16
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_07_16_ops_controls"
down_revision = "2026_07_15_user_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── users columns ────────────────────────────────────────────────
    user_cols = {col["name"] for col in inspector.get_columns("users")}
    with op.batch_alter_table("users") as batch_op:
        if "daily_limit_override" not in user_cols:
            batch_op.add_column(sa.Column("daily_limit_override", sa.Integer(), nullable=True))
        if "credit_usage_reset_at" not in user_cols:
            batch_op.add_column(sa.Column("credit_usage_reset_at", sa.DateTime(timezone=True), nullable=True))

    # ── admin_audit table ────────────────────────────────────────────
    if "admin_audit" not in set(inspector.get_table_names()):
        op.create_table(
            "admin_audit",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("actor", sa.String(length=320), nullable=False),
            sa.Column("action", sa.String(length=48), nullable=False),
            sa.Column("target_user_id", sa.String(length=36), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_admin_audit_created_at", "admin_audit", ["created_at"])
        op.create_index("ix_admin_audit_target", "admin_audit", ["target_user_id"])

    # ── runs indexes ─────────────────────────────────────────────────
    run_indexes = {ix["name"] for ix in inspector.get_indexes("runs")}
    if "ix_runs_user_created" not in run_indexes:
        op.create_index("ix_runs_user_created", "runs", ["user_id", "created_at"])
    if "ix_runs_created_at" not in run_indexes:
        op.create_index("ix_runs_created_at", "runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_user_created", table_name="runs")
    op.drop_index("ix_admin_audit_target", table_name="admin_audit")
    op.drop_index("ix_admin_audit_created_at", table_name="admin_audit")
    op.drop_table("admin_audit")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("credit_usage_reset_at")
        batch_op.drop_column("daily_limit_override")
