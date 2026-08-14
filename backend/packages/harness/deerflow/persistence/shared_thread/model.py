"""ORM model for the shared_threads table.

Public read-only thread sharing. A user opts a thread in by POSTing
/threads/{id}/share; the gateway stores an unguessable token and serves a
sanitized, read-only view of the conversation under GET /share/{token}
with no authentication. Revoking deletes the row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class SharedThreadRow(Base):
    __tablename__ = "shared_threads"

    token: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
