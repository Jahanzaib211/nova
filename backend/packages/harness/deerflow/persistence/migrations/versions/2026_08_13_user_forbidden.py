"""add is_forbidden to users

A "forbid" flag lets ops operators stop a user from authenticating without
deleting the account. The user keeps their data (so the operator can
inspect, then unforbid, then audit) but every login attempt returns 401.

The AuthMiddleware rejects forbidden users *before* the standard
password-hash check, so OAuth-only and password-only flows both hit the
gate. The admin endpoint ``POST /api/v1/admin/users/{id}/forbid`` and
``POST /api/v1/admin/users/{id}/unforbid`` flip the bit; both audit
``forbid-user`` / ``unforbid-user`` so the action is on the trail.

Default is 0 (i.e. False) so fresh deployments are unaffected. A server-
default of 0 makes the migration safe to run on existing rows.

Revision ID: 2026_08_13_user_forbidden
Revises: 2026_08_13_user_sign_in_tracking
Create Date: 2026-08-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_08_13_user_forbidden"
down_revision = "2026_08_13_user_sign_in_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("users")}

    with op.batch_alter_table("users") as batch_op:
        if "is_forbidden" not in existing:
            batch_op.add_column(
                sa.Column(
                    "is_forbidden",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        else:
            logger.info("users.is_forbidden already present — skipping")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_forbidden")
