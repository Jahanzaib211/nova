"""Email-marketing tables (``em_*``).

Every row is owner-scoped (``owner_user_id``) like ``jobs`` and ``runs``.
Statuses and event types are pinned to
``contracts/email_marketing_events_contract.json`` (``deerflow.email_marketing.events``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class EmListRow(Base):
    __tablename__ = "em_lists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_em_lists_owner_name"),)


class EmContactRow(Base):
    __tablename__ = "em_contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Lower-cased, trimmed: the identity we dedupe and suppress on.
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="subscribed", index=True)
    attributes_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("owner_user_id", "email_normalized", name="uq_em_contacts_owner_email"),)


class EmListMemberRow(Base):
    __tablename__ = "em_list_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("list_id", "contact_id", name="uq_em_list_members"),)


class EmTemplateRow(Base):
    __tablename__ = "em_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class EmCampaignRow(Base):
    __tablename__ = "em_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    list_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    from_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    throttle_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmSendRow(Base):
    """One message to one recipient of one campaign. The unit tracking keys on."""

    __tablename__ = "em_sends"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_em_sends_campaign_status", "campaign_id", "status"),)


class EmEventRow(Base):
    __tablename__ = "em_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    send_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)


class EmSuppressionRow(Base):
    __tablename__ = "em_suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("owner_user_id", "email_normalized", name="uq_em_suppressions_owner_email"),)


class EmBounceCursorRow(Base):
    """Where the IMAP poller got to, per owner mailbox."""

    __tablename__ = "em_bounce_cursor"

    owner_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
