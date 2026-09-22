"""IMAP polling of the bounce mailbox.

The VERP aliases (``bounce+*@``, ``unsubscribe+*@``) all land in one mailbox.
The poller fetches every message with a UID above the stored cursor
(``em_bounce_cursor``), classifies it (DSN, ARF, mailto-unsubscribe, other),
matches it to a send and records the outcome: hard bounces and complaints
suppress the address, an unsubscribe unsubscribes the contact. The IMAP
work is synchronous ``imaplib`` run in a worker thread.
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
import os
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from deerflow.config.email_marketing_config import BounceMailboxConfig
from deerflow.email_marketing.bounces import parse_bounce, send_id_from_message
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

logger = logging.getLogger(__name__)

_UNSUB_RE = re.compile(r"unsubscribe\+([A-Za-z0-9._-]+)@", re.IGNORECASE)
_REPLY_RE = re.compile(r"reply\+([A-Za-z0-9._-]+)@", re.IGNORECASE)


@dataclass(slots=True)
class Fetched:
    messages: list[tuple[int, bytes]] = field(default_factory=list)
    uidvalidity: int | None = None
    reset: bool = False


def _resolve(value: str) -> str:
    return os.environ.get(value[1:], "") if value.startswith("$") else value


def fetch_new_messages(cfg: BounceMailboxConfig, *, last_uid: int, uidvalidity: int | None, imap_factory: Callable[..., Any] | None = None, limit: int = 200) -> Fetched:
    """Blocking. Messages with UID > last_uid; everything when UIDVALIDITY changed."""
    factory = imap_factory
    if factory is None:
        factory = (lambda h, p: imaplib.IMAP4_SSL(h, p, ssl_context=ssl.create_default_context())) if cfg.use_ssl else imaplib.IMAP4
    client = factory(cfg.host, cfg.port)
    out = Fetched()
    try:
        client.login(cfg.username, _resolve(cfg.password))
        client.select(cfg.folder, readonly=True)
        _, validity = client.response("UIDVALIDITY")
        current = int(validity[0]) if validity and validity[0] else None
        out.uidvalidity = current
        start = last_uid + 1
        if current is not None and uidvalidity is not None and current != uidvalidity:
            out.reset = True
            start = 1
        status, data = client.uid("SEARCH", None, f"UID {start}:*")
        if status != "OK":
            return out
        uids = [int(u) for u in (data[0] or b"").split()]
        uids = [u for u in uids if u >= start][:limit]
        for uid in uids:
            status, payload = client.uid("FETCH", str(uid), "(RFC822)")
            if status != "OK":
                continue
            raw = next((part[1] for part in payload if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if raw is not None:
                out.messages.append((uid, raw))
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return out


class BouncePoller:
    def __init__(
        self,
        repo: EmailMarketingRepository,
        cfg: BounceMailboxConfig,
        *,
        imap_factory: Callable[..., Any] | None = None,
        on_reply: Callable[[dict[str, Any], bytes], Any] | None = None,
    ) -> None:
        self.repo = repo
        self.cfg = cfg
        self._imap_factory = imap_factory
        # Optional: hand a human reply (reply+<send>@) to a bridge (Chatwoot).
        self._on_reply = on_reply

    async def poll(self, owner: str) -> dict[str, int]:
        cursor = await self.repo.get_bounce_cursor(owner) or {}
        last_uid = int(cursor.get("last_uid") or 0)
        uidvalidity = cursor.get("uidvalidity")
        try:
            fetched = await asyncio.to_thread(fetch_new_messages, self.cfg, last_uid=last_uid, uidvalidity=uidvalidity, imap_factory=self._imap_factory)
        except Exception as exc:
            await self.repo.set_bounce_cursor(owner, mailbox=self.cfg.username, last_uid=last_uid, uidvalidity=uidvalidity, error=str(exc)[:500])
            raise
        summary = {"fetched": len(fetched.messages), "bounced_hard": 0, "bounced_soft": 0, "complained": 0, "unsubscribed": 0, "replied": 0, "unmatched": 0}
        high = last_uid if not fetched.reset else 0
        for uid, raw in fetched.messages:
            high = max(high, uid)
            await self._handle(owner, raw, summary)
        await self.repo.set_bounce_cursor(owner, mailbox=self.cfg.username, last_uid=high, uidvalidity=fetched.uidvalidity)
        return summary

    async def _handle(self, owner: str, raw: bytes, summary: dict[str, int]) -> None:
        send_id = send_id_from_message(raw)
        send = await self.repo.get_send(send_id) if send_id else None
        if send is None or send["owner_user_id"] != owner:
            summary["unmatched"] += 1
            return
        head = raw[:4096].decode("utf-8", "replace")
        if _UNSUB_RE.search(head):
            await self.repo.record_event(owner, type="unsubscribed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"via": "mailto"})
            await self.repo.unsubscribe_contact(owner, send["contact_id"], detail="mailto unsubscribe")
            await self.repo.record_event(owner, type="suppressed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"reason": "unsubscribe"})
            summary["unsubscribed"] += 1
            return
        parsed = parse_bounce(raw)
        if parsed.kind is None:
            if _REPLY_RE.search(head) and self._on_reply is not None:
                try:
                    result = self._on_reply(send, raw)
                    if asyncio.iscoroutine(result):
                        await result
                    summary["replied"] += 1
                    await self.repo.record_event(owner, type="replied", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"via": "chatwoot"})
                except Exception as exc:  # a bridge outage must not stall the poll
                    logger.warning("reply ingestion failed for send %s: %s", send["id"], exc)
                    summary["unmatched"] += 1
                return
            summary["unmatched"] += 1
            return
        await self.repo.record_event(owner, type=parsed.kind, campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"status": parsed.status, "diagnostic": parsed.diagnostic[:500]})
        summary[parsed.kind] += 1
        if parsed.kind == "bounced_hard":
            await self.repo.suppress(owner, send["email"], reason="hard_bounce", detail=parsed.diagnostic[:500] or parsed.status)
            await self.repo.mark_send(send["id"], status="failed", error=parsed.diagnostic or parsed.status)
            await self.repo.record_event(owner, type="suppressed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"reason": "hard_bounce"})
        elif parsed.kind == "complained":
            await self.repo.suppress(owner, send["email"], reason="complaint", detail=parsed.diagnostic[:500])
            await self.repo.record_event(owner, type="suppressed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"reason": "complaint"})
