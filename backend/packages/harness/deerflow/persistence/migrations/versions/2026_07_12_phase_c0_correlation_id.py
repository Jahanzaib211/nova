"""add correlation_id to runs

Phase C0.1 migration — backfill ``correlation_id`` column on existing
SQLite deployments.

The Phase C0 commit (``d4ac44a1``) added ``correlation_id`` to the
``RunRow`` ORM model and to the ``MemoryRunStore.put`` signature, but did
NOT include an Alembic migration. SQLAlchemy's ``Base.metadata.create_all()``
only creates tables that don't exist — it does not add columns to existing
tables. So the live SQLite database (deployed 2026-07-10, before Phase
C0) has the ``runs`` table WITHOUT the ``correlation_id`` column, and every
new run creation now fails with::

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
    no such column: runs.correlation_id

This migration is idempotent and safe to re-apply. It checks for the
column's existence via ``PRAGMA table_info`` before issuing the
``ALTER TABLE`` so that fresh deployments (where ``create_all`` already
added the column) are no-ops.

Revision ID: 2026_07_12_phase_c0_correlation_id
Revises: (none — this is the base revision)
Create Date: 2026-07-12

Why this is base, not a follow-up:
    - The ``alembic_version`` table does NOT yet exist on the live DB
      (no migration ever ran; the schema was created via ``create_all``).
    - Revises=None marks the base revision. Running ``alembic stamp``
      against this revision after the upgrade fixes ``alembic_version``
      tracking without re-running the DDL.

``down_revision`` is ``None``; future migrations should set theirs to
``2026_07_12_phase_c0_correlation_id``.
"""
from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic to walk the version chain.
revision = "2026_07_12_phase_c0_correlation_id"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``runs.correlation_id`` if the column is missing.

    Idempotent: re-running this migration on a DB that already has the
    column is a no-op. We deliberately do NOT use ``op.add_column``
    unconditionally because SQLite does not support ``ADD COLUMN IF
    NOT EXISTS`` until 3.35+, and a duplicate-column error would surface
    as an IntegrityError on subsequent runs.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("runs")}
    if "correlation_id" in existing:
        logger.info("runs.correlation_id already present — skipping")
        return
    logger.info("Adding runs.correlation_id VARCHAR(64) NULL")
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "correlation_id",
                sa.String(length=64),
                nullable=True,
            ),
        )


def downgrade() -> None:
    """Drop ``runs.correlation_id``. Reverses the Phase C0 migration."""
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("correlation_id")
