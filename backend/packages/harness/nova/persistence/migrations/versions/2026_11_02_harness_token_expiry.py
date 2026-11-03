"""Harness tokens: ``expires_at`` for per-turn runtime/delegate tokens.

Revision ID: 2026_11_02_harness_token_expiry
Revises: 2026_10_29_users_email_lower

Per-turn ``runtime:<thread>`` tokens are revoked when their ACP session
closes, but a revoke that fails (a cancelled session, a database blip) left a
bearer token granting the user's sandbox live forever: the table had no
expiry. Nullable, so existing rows (and human-minted tokens) keep no expiry;
the gateway's startup sweep revokes stale runtime tokens among them.

Idempotent like every version here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2026_11_02_harness_token_expiry"
down_revision: str | None = "2026_10_29_users_email_lower"
branch_labels = None
depends_on = None


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if "harness_tokens" not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns("harness_tokens")}


def upgrade() -> None:
    columns = _columns(op.get_bind())
    if not columns or "expires_at" in columns:
        return
    with op.batch_alter_table("harness_tokens") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if "expires_at" not in _columns(op.get_bind()):
        return
    with op.batch_alter_table("harness_tokens") as batch:
        batch.drop_column("expires_at")
