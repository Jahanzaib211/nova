"""ORM model for the user_api_keys table (bring-your-own-key).

Stores an encrypted per-user LLM provider API key. The key is encrypted at
rest with Fernet (see ``app.gateway.byok``); this table never holds
plaintext. One key per user for the MVP.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class UserApiKeyRow(Base):
    __tablename__ = "user_api_keys"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Logical provider label (e.g. "openai", "anthropic") — informational.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # Fernet-encrypted API key. Never stored or returned in plaintext.
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
