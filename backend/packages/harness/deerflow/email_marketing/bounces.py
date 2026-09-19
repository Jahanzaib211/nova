"""Bounce and complaint parsing.

Delivery Status Notifications (RFC 3464, ``multipart/report`` with a
``message/delivery-status`` part) and Abuse Reporting Format complaints
(RFC 5965, ``message/feedback-report``) are matched back to the send that
caused them by the VERP envelope address (``bounce+<send_id>@…``) or the
``X-Nova-Send`` header copied into the returned original.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import Message

_VERP_RE = re.compile(r"(?:bounce|unsubscribe)\+([A-Za-z0-9._-]+)@", re.IGNORECASE)
_XNOVA_RE = re.compile(r"^X-Nova-Send:\s*([A-Za-z0-9._-]+)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ParsedBounce:
    kind: str | None  # bounced_hard | bounced_soft | complained | None
    status: str | None = None
    action: str | None = None
    recipient: str | None = None
    diagnostic: str = ""


def classify_bounce(code: int, reply: str = "") -> str | None:
    """Map an SMTP reply code to an event type; None when it is not a failure."""
    if 200 <= code < 400:
        return None
    if 400 <= code < 500:
        return "deferred"
    text = reply.lower()
    # 552 (mailbox full) and quota wording are temporary even though 5xx.
    if code == 552 or "quota" in text or "mailbox full" in text or "over quota" in text:
        return "bounced_soft"
    return "bounced_hard"


def _walk(msg: Message):
    yield msg
    if msg.is_multipart():
        for part in msg.get_payload():
            yield from _walk(part)


def _fields(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


def _report_fields(part: Message) -> dict[str, str]:
    """``message/delivery-status`` and ``message/feedback-report`` bodies are
    header blocks; the stdlib parses them into sub-messages whose *headers*
    carry the fields (per-message block, then one per recipient). Merge
    them, later blocks winning, and fall back to raw text for odd MTAs."""
    payload = part.get_payload()
    if isinstance(payload, list) and payload and all(isinstance(p, Message) for p in payload):
        out: dict[str, str] = {}
        for sub in payload:
            for key, value in sub.items():
                out[key.lower()] = str(value).strip()
        if out:
            return out
    return _fields(_text(part))


def parse_bounce(raw: bytes) -> ParsedBounce:
    msg = message_from_bytes(raw, policy=policy.default)
    report_type = (msg.get_param("report-type", header="content-type") or "").lower()
    for part in _walk(msg):
        ctype = part.get_content_type()
        if ctype.endswith("/delivery-status") or (report_type == "delivery-status" and ctype == "text/plain" and "Action:" in _text(part)):
            fields = _report_fields(part)
            status = fields.get("status")
            action = fields.get("action", "").lower()
            recipient = fields.get("final-recipient") or fields.get("original-recipient") or ""
            recipient = recipient.split(";", 1)[-1].strip()
            diagnostic = fields.get("diagnostic-code", "").split(";", 1)[-1].strip()
            if action == "delayed" or (status or "").startswith("4"):
                kind = "bounced_soft"
            elif action == "failed" or (status or "").startswith("5"):
                kind = "bounced_hard"
            else:
                kind = None
            return ParsedBounce(kind=kind, status=status, action=action, recipient=recipient or None, diagnostic=diagnostic)
        if ctype.endswith("/feedback-report") or (report_type == "feedback-report" and ctype == "text/plain" and "Feedback-Type:" in _text(part)):
            fields = _report_fields(part)
            return ParsedBounce(kind="complained", action=fields.get("feedback-type"), diagnostic=fields.get("feedback-type", "abuse"))
    return ParsedBounce(kind=None)


def _text(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", "replace")
    except Exception:
        pass
    payload = part.get_payload()
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "\n".join(_text(p) for p in payload)
    return ""


def send_id_from_message(raw: bytes) -> str | None:
    """VERP first (``To``/``Delivered-To``/``Original-Mail-From``), then the
    ``X-Nova-Send`` header anywhere in the returned original."""
    msg = message_from_bytes(raw, policy=policy.default)
    for header in ("Delivered-To", "To", "X-Original-To", "Envelope-To"):
        for value in msg.get_all(header, []):
            m = _VERP_RE.search(str(value))
            if m:
                return m.group(1)
    for part in _walk(msg):
        m = _VERP_RE.search(_text(part)) if part.get_content_type().startswith(("message/", "text/")) else None
        if m and part is not msg:
            return m.group(1)
        m2 = _XNOVA_RE.search(_text(part))
        if m2:
            return m2.group(1)
    return None
