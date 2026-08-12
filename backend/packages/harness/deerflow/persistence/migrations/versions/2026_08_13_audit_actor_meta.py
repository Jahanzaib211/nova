"""add actor_ip + actor_user_agent to admin_audit

The admin_audit table is the append-only trail of operator actions. Until
now it only captured the *who* (actor) and the *what* (action). Without
the network address and the user agent, two different operators sharing
the same service account are indistinguishable in the audit log, and a
real production audit (SOC 2, ISO 27001 A.9.2.5) expects origin metadata.

This migration adds two nullable columns:

- ``actor_ip: VARCHAR(64)`` — the client IP the action came from, after
  the same ``AUTH_TRUSTED_PROXIES`` trust chain login uses. Stored as the
  raw string because CIDR-aware lookups happen at query time, not on the
  audit row.
- ``actor_user_agent: VARCHAR(512)`` — the ``User-Agent`` header verbatim.
  512 chars fits every practical browser UA and the longest automation
  client we have seen.

Both columns are nullable so existing rows (pre-migration) stay valid,
and so the new audit calls in the auth + admin routers can opt in
incrementally without rewriting historical rows.

Forward/backward migrations match the surrounding 2026_07_16 pattern:
idempotent column add via the SQLAlchemy inspector.

Revision ID: 2026_08_13_audit_actor_meta
Revises: 2026_07_16_ops_controls
Create Date: 2026-08-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_08_13_audit_actor_meta"
# The previous migration in the chain.
down_revision = "2026_07_17_b3_scoping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("admin_audit")}

    with op.batch_alter_table("admin_audit") as batch_op:
        if "actor_ip" not in existing:
            batch_op.add_column(sa.Column("actor_ip", sa.String(length=64), nullable=True))
        else:
            logger.info("admin_audit.actor_ip already present — skipping")
        if "actor_user_agent" not in existing:
            batch_op.add_column(sa.Column("actor_user_agent", sa.String(length=512), nullable=True))
        else:
            logger.info("admin_audit.actor_user_agent already present — skipping")


def downgrade() -> None:
    with op.batch_alter_table("admin_audit") as batch_op:
        batch_op.drop_column("actor_user_agent")
        batch_op.drop_column("actor_ip")
