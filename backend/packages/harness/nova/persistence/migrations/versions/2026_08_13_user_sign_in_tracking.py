"""add last_sign_in_at to users

The Overview page needs "when did this user last log in?" so operators can
spot dormant accounts and prioritise active ones. The previous schema has
``created_at`` (account open) but no per-account activity timestamp.

The LocalAuthProvider stamps ``last_sign_in_at`` on every successful
``authenticate(...)`` call. The admin ``GET /users/recent`` endpoint
surfaces it as ``last_sign_in_at`` in each row, alongside the aggregated
``last_run_at`` (derived from the ``runs`` table).

The column is nullable so users who have never logged in (e.g. accounts
created by an admin invite flow) still parse cleanly.

Revision ID: 2026_08_13_user_sign_in_tracking
Revises: 2026_08_13_audit_actor_meta
Create Date: 2026-08-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_08_13_user_sign_in_tracking"
down_revision = "2026_08_13_audit_actor_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("users")}

    with op.batch_alter_table("users") as batch_op:
        if "last_sign_in_at" not in existing:
            batch_op.add_column(sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True))
        else:
            logger.info("users.last_sign_in_at already present — skipping")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("last_sign_in_at")
