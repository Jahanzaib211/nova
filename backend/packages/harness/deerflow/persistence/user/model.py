"""ORM model for the users table.

Lives in the harness persistence package so it is picked up by
``Base.metadata.create_all()`` alongside ``threads_meta``, ``runs``,
``run_events``, and ``feedback``. Using the shared engine means:

- One SQLite/Postgres database, one connection pool
- One schema initialisation codepath
- Consistent async sessions across auth and persistence reads
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class UserRow(Base):
    __tablename__ = "users"

    # UUIDs are stored as 36-char strings for cross-backend portability.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # "admin" | "user" — kept as plain string to avoid ALTER TABLE pain
    # when new roles are introduced.
    system_role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # OAuth linkage (optional). A partial unique index enforces one
    # account per (provider, oauth_id) pair, leaving NULL/NULL rows
    # unconstrained so plain password accounts can coexist.
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Auth lifecycle flags
    needs_setup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_version: Mapped[int] = mapped_column(nullable=False, default=0)

    # Plan / billing. "free" | "plus" | "enterprise". Kept as plain string
    # to avoid ALTER TABLE churn when tiers change. plan_status mirrors the
    # Stripe subscription lifecycle ("active" | "past_due" | "canceled").
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free", server_default="free")
    plan_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    plan_renews_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Terms & Conditions consent trail. Records which TOS version the user
    # accepted and when, so a version bump can force re-acceptance.
    tos_accepted_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tos_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Referral flywheel. referral_code is this user's own invite code (unique);
    # referred_by is the code of whoever invited them (NULL for organic signups).
    referral_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True, index=True)
    referred_by: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Operator controls (set from the Ali Technologies ops console).
    # daily_limit_override replaces the plan's base daily token allowance when
    # set; credit_usage_reset_at marks the moment an operator reset the user's
    # usage — the credit meter only counts runs after max(utc_midnight, reset).
    daily_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_usage_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Account-level activity tracking. Stamped by LocalAuthProvider on every
    # successful authenticate(); surfaced by the ops console's "recent
    # signups" panel so operators can spot dormant accounts.
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Operator "forbid" toggle. When True, AuthMiddleware rejects the user
    # before any password / OAuth flow runs; the user keeps their data so
    # an admin can audit and unforbid without a destructive delete.
    is_forbidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))

    __table_args__ = (
        Index(
            "idx_users_oauth_identity",
            "oauth_provider",
            "oauth_id",
            unique=True,
            sqlite_where=text("oauth_provider IS NOT NULL AND oauth_id IS NOT NULL"),
        ),
    )
