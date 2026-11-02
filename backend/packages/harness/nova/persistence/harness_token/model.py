"""ORM row for harness tokens (see ``sql.py``)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class HarnessTokenRow(Base):
    __tablename__ = "harness_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: SHA-256 hex of the plaintext; the plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    #: First 12 characters, for display ("nhk_ab12cd34").
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Capability module ids this token may invoke, or ["*"].
    scopes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
