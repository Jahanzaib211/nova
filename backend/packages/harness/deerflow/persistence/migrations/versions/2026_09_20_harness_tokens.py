"""Harness tokens: bearer credentials for external harnesses (Claude Code,
OpenClaw) on Nova's MCP server.

Revision ID: 2026_09_20_harness_tokens
Revises: 2026_09_19_email_marketing

Idempotent like every version here: create_all at boot creates the table on
a fresh database, so this only acts on a database that predates it. Columns
mirror deerflow.persistence.harness_token.model exactly (pinned by
tests/test_schema_snapshot.py + test_migrations_apply_clean.py).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2026_09_20_harness_tokens"
down_revision: str | None = "2026_09_19_email_marketing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "harness_tokens" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "harness_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_harness_tokens_owner_user_id", "harness_tokens", ["owner_user_id"])
    op.create_index("ix_harness_tokens_token_hash", "harness_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("harness_tokens")
