"""ORM model for the credit_grants table.

A credit grant is a time-limited daily token bonus stacked on top of a
user's plan allowance. Referrals create two grants: a front-loaded welcome
boost for the new user and a longer boost for the referrer. The sum of a
user's *active* grants (``expires_at`` in the future) is added to their base
daily limit in the credit system.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class CreditGrantRow(Base):
    __tablename__ = "credit_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # Extra tokens per day this grant contributes while active.
    daily_bonus_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Free-form provenance, e.g. "referral_welcome" | "referral_referrer".
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("idx_credit_grants_user_expiry", "user_id", "expires_at"),)
