"""ORM model for the credit_requests table.

The self-service side of the hybrid credit wall: when a user hits their daily
limit they can submit a request for more instead of just emailing. Requests
land in the ops console, where an operator approves (which grants credits) or
declines. Email is denormalised so the console can show it without a join.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class CreditRequestRow(Base):
    __tablename__ = "credit_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # "pending" | "approved" | "declined"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (Index("ix_credit_requests_status_created", "status", "created_at"),)
