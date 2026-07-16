"""ORM model for the admin_audit table.

Append-only audit trail of operator (god-mode) actions taken through the
admin API — plan changes, usage resets, credit grants, limit overrides.
Every mutating admin endpoint writes one row. Read back via
``GET /api/v1/admin/audit`` and the ops console's Audit tab.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class AdminAuditRow(Base):
    __tablename__ = "admin_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Who acted: an admin user's email, or "ops-console" for service-token calls.
    actor: Mapped[str] = mapped_column(String(320), nullable=False)

    # What happened, kebab-case: "set-plan", "reset-usage", "grant-credits",
    # "set-limit", "reset-all-usage".
    action: Mapped[str] = mapped_column(String(48), nullable=False)

    # The user the action targeted (NULL for bulk actions).
    target_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Action parameters (plan chosen, tokens granted, limit value, ...).
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_admin_audit_created_at", "created_at"),
        Index("ix_admin_audit_target", "target_user_id"),
    )
