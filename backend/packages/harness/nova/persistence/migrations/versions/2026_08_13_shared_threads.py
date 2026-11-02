"""create shared_threads table (public read-only thread sharing)

Users can opt a thread into a public, read-only share link via
POST /threads/{id}/share. The token is unguessable and the public view
contains only the sanitized conversation (no feedback, no metadata).

Idempotent: creates the table only if absent. New tables are also created
by ``Base.metadata.create_all`` on startup, so this is belt-and-suspenders
for fresh Alembic-driven deploys.

Revision ID: 2026_08_13_shared_threads
Revises: 2026_08_13_prune_orphans
Create Date: 2026-08-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_08_13_shared_threads"
down_revision = "2026_08_13_prune_orphans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shared_threads" in set(inspector.get_table_names()):
        logger.info("shared_threads already present — skipping")
        return

    op.create_table(
        "shared_threads",
        sa.Column("token", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=36), nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shared_threads" in set(inspector.get_table_names()):
        op.drop_table("shared_threads")
