"""ORM model for the password_reset_tokens table.

One-shot, expiring password-reset tokens for the self-service
forgot-password flow. Only the SHA-256 hex digest of the token is stored;
the raw token travels once in the reset email. Tokens are single-use
(``consumed_at`` set on successful reset) and short-lived (TTL enforced
at validation time, 30 minutes).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class PasswordResetTokenRow(Base):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
