"""Batch SMTP delivery through the Mailcow relay.

One connection per batch, ``RSET`` between messages, one reconnect after a
dropped connection, reply codes classified into the contract's event types
(4xx → ``deferred``, 5xx → ``bounced_hard``/``bounced_soft``) and a
per-recipient-domain token bucket that *reports* throttled messages instead
of sleeping — the caller reschedules them. Everything here is synchronous
(``smtplib``); the job runs it in a worker thread.

Headers: VERP envelope sender ``bounce+<send_id>@<bounce_domain>`` so DSNs
name the send, ``List-Unsubscribe`` (https + mailto) with
``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` (RFC 8058),
``Precedence: bulk``, and ``X-Nova-Send`` as a second way home for MTAs that
quote the original instead of attaching it.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from deerflow.email_marketing.bounces import classify_bounce

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutboundMessage:
    send_id: str
    to: str
    subject: str
    html: str
    text: str
    unsubscribe_url: str | None = None
    unsubscribe_mailto: str | None = None


@dataclass(slots=True)
class SendResult:
    send_id: str
    status: str  # sent | deferred | bounced_hard | bounced_soft | throttled
    message_id: str | None = None
    error: str | None = None


class DomainThrottle:
    """Token bucket per recipient domain, refilled continuously."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = max(1, per_minute)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # domain -> (tokens, last)

    def allow(self, email: str) -> bool:
        domain = email.rsplit("@", 1)[-1].lower()
        now = self._clock()
        tokens, last = self._buckets.get(domain, (float(self.per_minute), now))
        tokens = min(float(self.per_minute), tokens + (now - last) * self.per_minute / 60.0)
        if tokens < 1.0:
            self._buckets[domain] = (tokens, now)
            return False
        self._buckets[domain] = (tokens - 1.0, now)
        return True


def build_message(
    msg: OutboundMessage,
    *,
    from_email: str,
    from_name: str,
    message_id: str,
    reply_to: str | None = None,
    unsubscribe_url: str | None = None,
    unsubscribe_mailto: str | None = None,
) -> bytes:
    em = EmailMessage()
    em["From"] = formataddr((from_name, from_email)) if from_name else from_email
    em["To"] = msg.to
    em["Subject"] = msg.subject
    em["Message-ID"] = message_id
    em["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    if reply_to:
        em["Reply-To"] = reply_to
    unsub = [f"<{u}>" for u in (unsubscribe_url or msg.unsubscribe_url, unsubscribe_mailto or msg.unsubscribe_mailto) if u]
    if unsub:
        em["List-Unsubscribe"] = ", ".join(unsub)
        em["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    em["Precedence"] = "bulk"
    em["Auto-Submitted"] = "auto-generated"
    em["X-Nova-Send"] = msg.send_id
    em.set_content(msg.text or "")
    em.add_alternative(msg.html or "", subtype="html")
    return bytes(em)


@dataclass
class SmtpSender:
    host: str
    port: int
    use_tls: bool
    use_ssl: bool
    username: str
    password: str
    from_email: str
    from_name: str
    bounce_domain: str
    reply_to: str | None = None
    timeout: float = 30.0
    throttle: DomainThrottle | None = None
    smtp_factory: Callable[..., object] | None = None
    _conn: object | None = field(default=None, init=False, repr=False)

    # -- connection ------------------------------------------------------

    def _connect(self):
        factory = self.smtp_factory
        if factory is None:
            factory = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        kwargs = {"timeout": self.timeout}
        if self.use_ssl and self.smtp_factory is None:
            kwargs["context"] = ssl.create_default_context()
        conn = factory(self.host, self.port, **kwargs)
        conn.ehlo()
        if self.use_tls and not self.use_ssl:
            conn.starttls(context=ssl.create_default_context())
            conn.ehlo()
        if self.username:
            conn.login(self.username, self.password)
        return conn

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.quit()
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None

    # -- sending ----------------------------------------------------------

    def envelope_from(self, send_id: str) -> str:
        domain = self.bounce_domain or self.from_email.rsplit("@", 1)[-1]
        return f"bounce+{send_id}@{domain}"

    def _deliver(self, msg: OutboundMessage) -> SendResult:
        message_id = make_msgid(idstring=msg.send_id, domain=self.from_email.rsplit("@", 1)[-1])
        raw = build_message(msg, from_email=self.from_email, from_name=self.from_name, message_id=message_id, reply_to=self.reply_to)
        try:
            self._conn.sendmail(self.envelope_from(msg.send_id), [msg.to], raw)
            return SendResult(msg.send_id, "sent", message_id=message_id)
        except smtplib.SMTPRecipientsRefused as exc:
            code, text = next(iter(exc.recipients.values()), (550, b""))
            reply = text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text)
            status = classify_bounce(int(code), reply) or "bounced_hard"
            return SendResult(msg.send_id, status, error=f"{code} {reply}")
        except smtplib.SMTPResponseException as exc:
            reply = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            status = classify_bounce(int(exc.smtp_code), reply) or "deferred"
            return SendResult(msg.send_id, status, error=f"{exc.smtp_code} {reply}")

    def send_batch(self, messages: list[OutboundMessage]) -> list[SendResult]:
        results: list[SendResult] = []
        reconnected = False
        try:
            for i, msg in enumerate(messages):
                if self.throttle is not None and not self.throttle.allow(msg.to):
                    results.append(SendResult(msg.send_id, "throttled"))
                    continue
                if self._conn is None:
                    try:
                        self._conn = self._connect()
                    except (OSError, smtplib.SMTPException) as exc:
                        results.append(SendResult(msg.send_id, "deferred", error=f"connect: {exc}"))
                        continue
                elif i > 0:
                    try:
                        self._conn.rset()
                    except (OSError, smtplib.SMTPException):
                        self._close()
                        self._conn = self._connect()
                try:
                    results.append(self._deliver(msg))
                except (smtplib.SMTPServerDisconnected, OSError) as exc:
                    self._close()
                    results.append(SendResult(msg.send_id, "deferred", error=f"disconnected: {exc}"))
                    if not reconnected:
                        reconnected = True  # the next message reconnects lazily
                    continue
        finally:
            self._close()
        return results
