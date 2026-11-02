"""create credit_grants table

Phase C9.2 — backs the referral flywheel. A credit grant is a time-limited
daily token bonus (welcome boost for new referred users, referrer boost per
successful invite). The credit system sums a user's active grants on top of
their plan allowance.

Idempotent: creates the table only if it doesn't already exist, so
deployments where ``create_all`` already made it are a no-op.

Revision ID: 2026_07_15_credit_grants
Revises: 2026_07_15_nova_plus_user_columns
Create Date: 2026-07-15
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_07_15_credit_grants"
down_revision = "2026_07_15_nova_plus_user_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "credit_grants" in set(inspector.get_table_names()):
        logger.info("credit_grants already present — skipping")
        return

    op.create_table(
        "credit_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("daily_bonus_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_credit_grants_user_id", "credit_grants", ["user_id"])
    op.create_index("idx_credit_grants_user_expiry", "credit_grants", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_credit_grants_user_expiry", table_name="credit_grants")
    op.drop_index("ix_credit_grants_user_id", table_name="credit_grants")
    op.drop_table("credit_grants")
