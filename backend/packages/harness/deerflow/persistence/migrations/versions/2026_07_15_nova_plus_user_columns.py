"""add plan, billing, consent and referral columns to users

Phase C9.1 migration — extend the ``users`` table for Nova Plus (plan +
Stripe billing), enterprise Terms consent tracking, and the referral
flywheel.

Background mirrors ``2026_07_12_phase_c0_correlation_id``: the live SQLite
database created the ``users`` table via ``Base.metadata.create_all()``,
which never adds columns to an existing table. These new ORM columns
(``plan``, ``plan_status``, ``plan_renews_at``, ``stripe_customer_id``,
``stripe_subscription_id``, ``tos_accepted_version``, ``tos_accepted_at``,
``referral_code``, ``referred_by``) therefore have to be added by an
explicit ``ALTER TABLE`` so the ~24 existing accounts backfill cleanly.

Idempotent: each column is added only if missing (``PRAGMA table_info``
via the SQLAlchemy inspector), so fresh deployments where ``create_all``
already created the columns are a no-op.

Revision ID: 2026_07_15_nova_plus_user_columns
Revises: 2026_07_12_phase_c0_correlation_id
Create Date: 2026-07-15
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic to walk the version chain.
revision = "2026_07_15_nova_plus_user_columns"
down_revision = "2026_07_12_phase_c0_correlation_id"
branch_labels = None
depends_on = None


# (column_name, column_factory) — factory called per add so we never share a
# Column instance across the metadata and the ALTER (SQLAlchemy forbids reuse).
_NEW_COLUMNS: tuple[tuple[str, Callable[[], sa.Column]], ...] = (
    ("plan", lambda: sa.Column("plan", sa.String(length=16), nullable=False, server_default="free")),
    ("plan_status", lambda: sa.Column("plan_status", sa.String(length=16), nullable=True)),
    ("plan_renews_at", lambda: sa.Column("plan_renews_at", sa.DateTime(timezone=True), nullable=True)),
    ("stripe_customer_id", lambda: sa.Column("stripe_customer_id", sa.String(length=64), nullable=True)),
    ("stripe_subscription_id", lambda: sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True)),
    ("tos_accepted_version", lambda: sa.Column("tos_accepted_version", sa.String(length=32), nullable=True)),
    ("tos_accepted_at", lambda: sa.Column("tos_accepted_at", sa.DateTime(timezone=True), nullable=True)),
    ("referral_code", lambda: sa.Column("referral_code", sa.String(length=16), nullable=True)),
    ("referred_by", lambda: sa.Column("referred_by", sa.String(length=16), nullable=True)),
)


def upgrade() -> None:
    """Add the new ``users`` columns + indexes if missing (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("users")}
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("users")}

    with op.batch_alter_table("users") as batch_op:
        for name, factory in _NEW_COLUMNS:
            if name in existing:
                logger.info("users.%s already present — skipping", name)
                continue
            logger.info("Adding users.%s", name)
            batch_op.add_column(factory())

    # Indexes to match the ORM (stripe_customer_id lookup on webhooks,
    # referral_code uniqueness for invite resolution). Created outside the
    # batch so re-runs on an already-migrated DB stay no-ops.
    if "ix_users_stripe_customer_id" not in existing_indexes:
        op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"])
    if "ix_users_referral_code" not in existing_indexes:
        op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)


def downgrade() -> None:
    """Drop the columns + indexes added by :func:`upgrade`."""
    with op.batch_alter_table("users") as batch_op:
        for name, _ in reversed(_NEW_COLUMNS):
            batch_op.drop_column(name)
