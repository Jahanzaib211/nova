"""create user_api_keys table (bring-your-own-key)

Phase C9.3 — stores an encrypted per-user LLM provider API key so users can
run against their own account and bypass Nova's credit wall. Feature is
gated off by default (see ``app.gateway.byok``); the table is harmless when
unused.

Idempotent: creates the table only if absent.

Revision ID: 2026_07_15_user_api_keys
Revises: 2026_07_15_credit_grants
Create Date: 2026-07-15
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_07_15_user_api_keys"
down_revision = "2026_07_15_credit_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_api_keys" in set(inspector.get_table_names()):
        logger.info("user_api_keys already present — skipping")
        return

    op.create_table(
        "user_api_keys",
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_api_keys")
