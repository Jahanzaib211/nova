"""Email-marketing repository.

One class, one session per call, no ORM objects escape: every method returns
plain dicts. Every read and write is scoped by ``owner_user_id`` except the
send-level operations the worker and the public tracking endpoints use,
which key on ids only they can know.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.email_marketing.model import (
    EmBounceCursorRow,
    EmCampaignRow,
    EmContactRow,
    EmEventRow,
    EmListMemberRow,
    EmListRow,
    EmSendRow,
    EmSuppressionRow,
    EmTemplateRow,
)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


class EmailMarketingRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ---------------------------------------------------------------- shaping

    @staticmethod
    def _list(row: EmListRow, member_count: int = 0) -> dict[str, Any]:
        return {"id": row.id, "name": row.name, "description": row.description, "member_count": member_count, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}

    @staticmethod
    def _contact(row: EmContactRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "email": row.email,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "status": row.status,
            "attributes": dict(row.attributes_json or {}),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _template(row: EmTemplateRow) -> dict[str, Any]:
        return {"id": row.id, "name": row.name, "subject": row.subject, "html": row.html, "text": row.text, "version": row.version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}

    @staticmethod
    def _campaign(row: EmCampaignRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "list_id": row.list_id,
            "template_id": row.template_id,
            "from_email": row.from_email,
            "from_name": row.from_name,
            "reply_to": row.reply_to,
            "status": row.status,
            "scheduled_at": _iso(row.scheduled_at),
            "throttle_per_minute": row.throttle_per_minute,
            "stats": dict(row.stats_json or {}),
            "job_id": row.job_id,
            "error": row.error,
            "created_at": _iso(row.created_at),
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
        }

    @staticmethod
    def _send(row: EmSendRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "owner_user_id": row.owner_user_id,
            "campaign_id": row.campaign_id,
            "contact_id": row.contact_id,
            "email": row.email,
            "message_id": row.message_id,
            "status": row.status,
            "attempts": row.attempts,
            "last_error": row.last_error,
            "sent_at": _iso(row.sent_at),
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _event(row: EmEventRow) -> dict[str, Any]:
        return {"id": row.id, "type": row.type, "campaign_id": row.campaign_id, "send_id": row.send_id, "contact_id": row.contact_id, "payload": dict(row.payload_json or {}), "created_at": _iso(row.created_at)}

    # ------------------------------------------------------------------ lists

    async def create_list(self, owner: str, *, name: str, description: str | None = None) -> dict[str, Any]:
        async with self._sf() as s:
            row = EmListRow(id=str(uuid4()), owner_user_id=owner, name=name.strip(), description=description)
            s.add(row)
            await s.commit()
            return self._list(row)

    async def list_lists(self, owner: str) -> list[dict[str, Any]]:
        async with self._sf() as s:
            counts = select(EmListMemberRow.list_id, func.count().label("n")).group_by(EmListMemberRow.list_id).subquery()
            rows = (await s.execute(select(EmListRow, counts.c.n).outerjoin(counts, counts.c.list_id == EmListRow.id).where(EmListRow.owner_user_id == owner).order_by(EmListRow.created_at.desc()))).all()
            return [self._list(row, int(n or 0)) for row, n in rows]

    async def get_list(self, owner: str, list_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmListRow, list_id)
            if row is None or row.owner_user_id != owner:
                return None
            n = (await s.execute(select(func.count()).select_from(EmListMemberRow).where(EmListMemberRow.list_id == list_id))).scalar_one()
            return self._list(row, int(n))

    async def update_list(self, owner: str, list_id: str, **fields: Any) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmListRow, list_id)
            if row is None or row.owner_user_id != owner:
                return None
            for k, v in fields.items():
                if v is not None and k in ("name", "description"):
                    setattr(row, k, v.strip() if k == "name" else v)
            await s.commit()
            n = (await s.execute(select(func.count()).select_from(EmListMemberRow).where(EmListMemberRow.list_id == list_id))).scalar_one()
            return self._list(row, int(n))

    async def delete_list(self, owner: str, list_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EmListRow, list_id)
            if row is None or row.owner_user_id != owner:
                return False
            await s.execute(delete(EmListMemberRow).where(EmListMemberRow.list_id == list_id))
            await s.delete(row)
            await s.commit()
            return True

    async def add_members(self, owner: str, list_id: str, contact_ids: list[str]) -> int:
        async with self._sf() as s:
            lst = await s.get(EmListRow, list_id)
            if lst is None or lst.owner_user_id != owner:
                return 0
            owned = set((await s.execute(select(EmContactRow.id).where(EmContactRow.owner_user_id == owner, EmContactRow.id.in_(contact_ids)))).scalars().all())
            existing = set((await s.execute(select(EmListMemberRow.contact_id).where(EmListMemberRow.list_id == list_id, EmListMemberRow.contact_id.in_(contact_ids)))).scalars().all())
            added = 0
            for cid in contact_ids:
                if cid in owned and cid not in existing:
                    s.add(EmListMemberRow(list_id=list_id, contact_id=cid))
                    existing.add(cid)
                    added += 1
            await s.commit()
            return added

    async def remove_member(self, owner: str, list_id: str, contact_id: str) -> bool:
        async with self._sf() as s:
            lst = await s.get(EmListRow, list_id)
            if lst is None or lst.owner_user_id != owner:
                return False
            res = await s.execute(delete(EmListMemberRow).where(EmListMemberRow.list_id == list_id, EmListMemberRow.contact_id == contact_id))
            await s.commit()
            return bool(res.rowcount)

    async def list_members(self, owner: str, list_id: str, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        async with self._sf() as s:
            lst = await s.get(EmListRow, list_id)
            if lst is None or lst.owner_user_id != owner:
                return []
            rows = (
                (await s.execute(select(EmContactRow).join(EmListMemberRow, EmListMemberRow.contact_id == EmContactRow.id).where(EmListMemberRow.list_id == list_id).order_by(EmContactRow.email_normalized).limit(limit).offset(offset)))
                .scalars()
                .all()
            )
            return [self._contact(r) for r in rows]

    # --------------------------------------------------------------- contacts

    async def upsert_contact(self, owner: str, *, email: str, first_name: str | None = None, last_name: str | None = None, status: str | None = None, attributes: dict | None = None) -> dict[str, Any]:
        norm = normalize_email(email)
        async with self._sf() as s:
            row = (await s.execute(select(EmContactRow).where(EmContactRow.owner_user_id == owner, EmContactRow.email_normalized == norm))).scalar_one_or_none()
            if row is None:
                row = EmContactRow(id=str(uuid4()), owner_user_id=owner, email=email.strip(), email_normalized=norm, first_name=first_name, last_name=last_name, status=status or "subscribed", attributes_json=dict(attributes or {}))
                s.add(row)
            else:
                if first_name is not None:
                    row.first_name = first_name
                if last_name is not None:
                    row.last_name = last_name
                if status is not None:
                    row.status = status
                if attributes:
                    row.attributes_json = {**(row.attributes_json or {}), **attributes}
            await s.commit()
            return self._contact(row)

    async def get_contact(self, owner: str, contact_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmContactRow, contact_id)
            return self._contact(row) if row is not None and row.owner_user_id == owner else None

    async def list_contacts(self, owner: str, *, query: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        async with self._sf() as s:
            stmt = select(EmContactRow).where(EmContactRow.owner_user_id == owner)
            if status:
                stmt = stmt.where(EmContactRow.status == status)
            if query:
                q = f"%{normalize_email(query)}%"
                stmt = stmt.where(EmContactRow.email_normalized.like(q))
            total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
            rows = (await s.execute(stmt.order_by(EmContactRow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
            return {"contacts": [self._contact(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}

    async def delete_contact(self, owner: str, contact_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EmContactRow, contact_id)
            if row is None or row.owner_user_id != owner:
                return False
            await s.execute(delete(EmListMemberRow).where(EmListMemberRow.contact_id == contact_id))
            await s.delete(row)
            await s.commit()
            return True

    async def unsubscribe_contact(self, owner: str, contact_id: str, *, detail: str | None = None) -> bool:
        async with self._sf() as s:
            row = await s.get(EmContactRow, contact_id)
            if row is None or row.owner_user_id != owner:
                return False
            row.status = "unsubscribed"
            await s.commit()
            email = row.email_normalized
        await self.suppress(owner, email, reason="unsubscribe", detail=detail)
        return True

    # ------------------------------------------------------------ suppressions

    async def suppress(self, owner: str, email: str, *, reason: str, detail: str | None = None) -> dict[str, Any]:
        norm = normalize_email(email)
        async with self._sf() as s:
            row = (await s.execute(select(EmSuppressionRow).where(EmSuppressionRow.owner_user_id == owner, EmSuppressionRow.email_normalized == norm))).scalar_one_or_none()
            if row is None:
                row = EmSuppressionRow(owner_user_id=owner, email_normalized=norm, reason=reason, detail=detail)
                s.add(row)
            else:
                row.reason, row.detail = reason, detail
            # Mirror onto the contact so lists show why someone is not mailed.
            contact_status = {"hard_bounce": "bounced", "complaint": "complained", "unsubscribe": "unsubscribed"}.get(reason)
            if contact_status:
                await s.execute(update(EmContactRow).where(EmContactRow.owner_user_id == owner, EmContactRow.email_normalized == norm).values(status=contact_status))
            await s.commit()
            return {"email": row.email_normalized, "reason": row.reason, "detail": row.detail, "created_at": _iso(row.created_at)}

    async def unsuppress(self, owner: str, email: str) -> bool:
        async with self._sf() as s:
            res = await s.execute(delete(EmSuppressionRow).where(EmSuppressionRow.owner_user_id == owner, EmSuppressionRow.email_normalized == normalize_email(email)))
            await s.commit()
            return bool(res.rowcount)

    async def is_suppressed(self, owner: str, email: str) -> bool:
        async with self._sf() as s:
            return (await s.execute(select(func.count()).select_from(EmSuppressionRow).where(EmSuppressionRow.owner_user_id == owner, EmSuppressionRow.email_normalized == normalize_email(email)))).scalar_one() > 0

    async def list_suppressions(self, owner: str, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = (await s.execute(select(EmSuppressionRow).where(EmSuppressionRow.owner_user_id == owner).order_by(EmSuppressionRow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
            return [{"email": r.email_normalized, "reason": r.reason, "detail": r.detail, "created_at": _iso(r.created_at)} for r in rows]

    async def recipients_for_list(self, owner: str, list_id: str) -> list[dict[str, Any]]:
        """Subscribed members of a list minus every suppressed address."""
        async with self._sf() as s:
            lst = await s.get(EmListRow, list_id)
            if lst is None or lst.owner_user_id != owner:
                return []
            suppressed = select(EmSuppressionRow.email_normalized).where(EmSuppressionRow.owner_user_id == owner)
            rows = (
                (
                    await s.execute(
                        select(EmContactRow)
                        .join(EmListMemberRow, EmListMemberRow.contact_id == EmContactRow.id)
                        .where(EmListMemberRow.list_id == list_id, EmContactRow.status == "subscribed", EmContactRow.email_normalized.not_in(suppressed))
                        .order_by(EmContactRow.email_normalized)
                    )
                )
                .scalars()
                .all()
            )
            return [self._contact(r) for r in rows]

    # -------------------------------------------------------------- templates

    async def create_template(self, owner: str, *, name: str, subject: str, html: str, text: str | None = None) -> dict[str, Any]:
        async with self._sf() as s:
            row = EmTemplateRow(id=str(uuid4()), owner_user_id=owner, name=name.strip(), subject=subject, html=html, text=text)
            s.add(row)
            await s.commit()
            return self._template(row)

    async def list_templates(self, owner: str) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = (await s.execute(select(EmTemplateRow).where(EmTemplateRow.owner_user_id == owner).order_by(EmTemplateRow.updated_at.desc()))).scalars().all()
            return [self._template(r) for r in rows]

    async def get_template(self, owner: str, template_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmTemplateRow, template_id)
            return self._template(row) if row is not None and row.owner_user_id == owner else None

    async def update_template(self, owner: str, template_id: str, **fields: Any) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmTemplateRow, template_id)
            if row is None or row.owner_user_id != owner:
                return None
            changed = False
            for k in ("name", "subject", "html", "text"):
                if k in fields and fields[k] is not None and getattr(row, k) != fields[k]:
                    setattr(row, k, fields[k])
                    changed = True
            if changed:
                row.version += 1
            await s.commit()
            return self._template(row)

    async def delete_template(self, owner: str, template_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EmTemplateRow, template_id)
            if row is None or row.owner_user_id != owner:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # -------------------------------------------------------------- campaigns

    async def create_campaign(
        self, owner: str, *, name: str, list_id: str, template_id: str, from_email: str, from_name: str, reply_to: str | None = None, scheduled_at: datetime | None = None, throttle_per_minute: int | None = None
    ) -> dict[str, Any]:
        async with self._sf() as s:
            row = EmCampaignRow(
                id=str(uuid4()),
                owner_user_id=owner,
                name=name.strip(),
                list_id=list_id,
                template_id=template_id,
                from_email=from_email,
                from_name=from_name,
                reply_to=reply_to,
                scheduled_at=scheduled_at,
                throttle_per_minute=throttle_per_minute,
                status="scheduled" if scheduled_at else "draft",
            )
            s.add(row)
            await s.commit()
            return self._campaign(row)

    async def list_campaigns(self, owner: str) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = (await s.execute(select(EmCampaignRow).where(EmCampaignRow.owner_user_id == owner).order_by(EmCampaignRow.created_at.desc()))).scalars().all()
            return [self._campaign(r) for r in rows]

    async def get_campaign(self, owner: str, campaign_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmCampaignRow, campaign_id)
            return self._campaign(row) if row is not None and row.owner_user_id == owner else None

    async def get_campaign_any_owner(self, campaign_id: str) -> dict[str, Any] | None:
        """Worker path: the job carries the owner; the row is authoritative."""
        async with self._sf() as s:
            row = await s.get(EmCampaignRow, campaign_id)
            return {**self._campaign(row), "owner_user_id": row.owner_user_id} if row is not None else None

    async def update_campaign(self, owner: str, campaign_id: str, **fields: Any) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmCampaignRow, campaign_id)
            if row is None or row.owner_user_id != owner:
                return None
            for k, v in fields.items():
                if k in ("name", "list_id", "template_id", "from_email", "from_name", "reply_to", "scheduled_at", "throttle_per_minute") and v is not None:
                    setattr(row, k, v.strip() if k == "name" else v)
            if "scheduled_at" in fields and row.status in ("draft", "scheduled"):
                row.status = "scheduled" if row.scheduled_at else "draft"
            await s.commit()
            return self._campaign(row)

    async def update_campaign_status(self, owner: str, campaign_id: str, status: str, *, job_id: str | None = None, error: str | None = None, stats: dict | None = None) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmCampaignRow, campaign_id)
            if row is None or row.owner_user_id != owner:
                return None
            row.status = status
            if job_id is not None:
                row.job_id = job_id
            if error is not None:
                row.error = error
            if stats is not None:
                row.stats_json = stats
            if status == "sending" and row.started_at is None:
                row.started_at = _now()
            if status in ("completed", "cancelled", "failed"):
                row.finished_at = _now()
            await s.commit()
            return self._campaign(row)

    async def delete_campaign(self, owner: str, campaign_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EmCampaignRow, campaign_id)
            if row is None or row.owner_user_id != owner or row.status in ("sending", "paused"):
                return False
            await s.execute(delete(EmSendRow).where(EmSendRow.campaign_id == campaign_id))
            await s.delete(row)
            await s.commit()
            return True

    # ------------------------------------------------------------------ sends

    async def create_sends(self, owner: str, campaign_id: str, recipients: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with self._sf() as s:
            existing = set((await s.execute(select(EmSendRow.contact_id).where(EmSendRow.campaign_id == campaign_id))).scalars().all())
            rows = []
            for r in recipients:
                if r["id"] in existing:
                    continue
                row = EmSendRow(id=str(uuid4()), owner_user_id=owner, campaign_id=campaign_id, contact_id=r["id"], email=r["email"])
                s.add(row)
                rows.append(row)
            await s.commit()
            return [self._send(r) for r in rows]

    async def claim_queued_sends(self, campaign_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Move up to ``limit`` queued sends to ``sending`` and return them."""
        async with self._sf() as s:
            rows = (await s.execute(select(EmSendRow).where(EmSendRow.campaign_id == campaign_id, EmSendRow.status == "queued").order_by(EmSendRow.created_at).limit(limit))).scalars().all()
            for row in rows:
                row.status = "sending"
                row.attempts += 1
            await s.commit()
            return [self._send(r) for r in rows]

    async def requeue_sending(self, campaign_id: str) -> int:
        """Return anything stuck in ``sending`` (a killed batch) to the queue."""
        async with self._sf() as s:
            res = await s.execute(update(EmSendRow).where(EmSendRow.campaign_id == campaign_id, EmSendRow.status == "sending").values(status="queued"))
            await s.commit()
            return int(res.rowcount or 0)

    async def mark_send(self, send_id: str, *, status: str, message_id: str | None = None, error: str | None = None) -> None:
        async with self._sf() as s:
            row = await s.get(EmSendRow, send_id)
            if row is None:
                return
            row.status = status
            if message_id:
                row.message_id = message_id
            if error is not None:
                row.last_error = error[:2000]
            if status == "sent":
                row.sent_at = _now()
            await s.commit()

    async def get_send(self, send_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmSendRow, send_id)
            return self._send(row) if row is not None else None

    async def find_send_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = (await s.execute(select(EmSendRow).where(EmSendRow.message_id == message_id))).scalar_one_or_none()
            return self._send(row) if row is not None else None

    async def list_sends(self, owner: str, campaign_id: str, *, status: str | None = None, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        async with self._sf() as s:
            stmt = select(EmSendRow).where(EmSendRow.owner_user_id == owner, EmSendRow.campaign_id == campaign_id)
            if status:
                stmt = stmt.where(EmSendRow.status == status)
            rows = (await s.execute(stmt.order_by(EmSendRow.created_at).limit(limit).offset(offset))).scalars().all()
            return [self._send(r) for r in rows]

    async def send_counts(self, campaign_id: str) -> dict[str, int]:
        async with self._sf() as s:
            rows = (await s.execute(select(EmSendRow.status, func.count()).where(EmSendRow.campaign_id == campaign_id).group_by(EmSendRow.status))).all()
            return {status: int(n) for status, n in rows}

    # ----------------------------------------------------------------- events

    async def record_event(self, owner: str, *, type: str, campaign_id: str | None = None, send_id: str | None = None, contact_id: str | None = None, payload: dict | None = None) -> dict[str, Any]:
        async with self._sf() as s:
            row = EmEventRow(owner_user_id=owner, campaign_id=campaign_id, send_id=send_id, contact_id=contact_id, type=type, payload_json=dict(payload or {}))
            s.add(row)
            await s.commit()
            return self._event(row)

    async def list_events(self, owner: str, *, campaign_id: str | None = None, contact_id: str | None = None, type: str | None = None, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        async with self._sf() as s:
            stmt = select(EmEventRow).where(EmEventRow.owner_user_id == owner)
            if campaign_id:
                stmt = stmt.where(EmEventRow.campaign_id == campaign_id)
            if contact_id:
                stmt = stmt.where(EmEventRow.contact_id == contact_id)
            if type:
                stmt = stmt.where(EmEventRow.type == type)
            rows = (await s.execute(stmt.order_by(EmEventRow.id.desc()).limit(limit).offset(offset))).scalars().all()
            return [self._event(r) for r in rows]

    async def has_event(self, send_id: str, type: str) -> bool:
        async with self._sf() as s:
            return (await s.execute(select(func.count()).select_from(EmEventRow).where(EmEventRow.send_id == send_id, EmEventRow.type == type))).scalar_one() > 0

    async def prune_events(self, older_than: datetime) -> int:
        async with self._sf() as s:
            res = await s.execute(delete(EmEventRow).where(EmEventRow.created_at < older_than))
            await s.commit()
            return int(res.rowcount or 0)

    async def campaign_stats(self, owner: str, campaign_id: str) -> dict[str, Any] | None:
        """Send counts by status plus unique-per-send event counts."""
        async with self._sf() as s:
            camp = await s.get(EmCampaignRow, campaign_id)
            if camp is None or camp.owner_user_id != owner:
                return None
            counts = await self.send_counts(campaign_id)
            uniques = (await s.execute(select(EmEventRow.type, func.count(func.distinct(EmEventRow.send_id))).where(EmEventRow.campaign_id == campaign_id).group_by(EmEventRow.type))).all()
            stats: dict[str, Any] = {
                "recipients": sum(counts.values()),
                "queued": counts.get("queued", 0),
                "sending": counts.get("sending", 0),
                "sent": counts.get("sent", 0),
                "deferred": counts.get("deferred", 0),
                "failed": counts.get("failed", 0),
                "suppressed": counts.get("suppressed", 0),
            }
            for type_, n in uniques:
                if type_ in ("opened", "clicked", "bounced_hard", "bounced_soft", "complained", "unsubscribed"):
                    stats[type_] = int(n)
            for key in ("opened", "clicked", "bounced_hard", "bounced_soft", "complained", "unsubscribed"):
                stats.setdefault(key, 0)
            return stats

    # ------------------------------------------------------------ bounce cursor

    async def get_bounce_cursor(self, owner: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = await s.get(EmBounceCursorRow, owner)
            if row is None:
                return None
            return {"mailbox": row.mailbox, "last_uid": row.last_uid, "uidvalidity": row.uidvalidity, "last_polled_at": _iso(row.last_polled_at), "last_error": row.last_error, "has_processed": row.has_processed}

    async def set_bounce_cursor(self, owner: str, *, mailbox: str, last_uid: int, uidvalidity: int | None, error: str | None = None) -> None:
        async with self._sf() as s:
            row = await s.get(EmBounceCursorRow, owner)
            if row is None:
                row = EmBounceCursorRow(owner_user_id=owner)
                s.add(row)
            row.mailbox = mailbox
            row.last_uid = last_uid
            row.uidvalidity = uidvalidity
            row.last_polled_at = _now()
            row.last_error = error
            row.has_processed = True
            await s.commit()
