"""create credit_requests table (self-service credit requests)

Phase C9.5 — the self-service side of the hybrid credit wall. Users who hit
their daily limit submit a request; operators approve (granting credits) or
decline from the ops console.

Idempotent: creates the table only if absent. New tables are also created by
``Base.metadata.create_all`` on startup, so this is belt-and-suspenders for
fresh Alembic-driven deploys.

Revision ID: 2026_07_16_credit_requests
Revises: 2026_07_16_ops_controls
Create Date: 2026-07-16
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_07_16_credit_requests"
down_revision = "2026_07_16_ops_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "credit_requests" in set(inspector.get_table_names()):
        logger.info("credit_requests already present — skipping")
        return

    op.create_table(
        "credit_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=320), nullable=True),
    )
    op.create_index("ix_credit_requests_user_id", "credit_requests", ["user_id"])
    op.create_index("ix_credit_requests_status_created", "credit_requests", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_credit_requests_status_created", table_name="credit_requests")
    op.drop_index("ix_credit_requests_user_id", table_name="credit_requests")
    op.drop_table("credit_requests")
