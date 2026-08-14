"""Outbound email delivery via SMTP, off the event loop.

The gateway has no persistent mailer process — every send opens a fresh
connection. SMTP is blocking IO, so :func:`send_email` offloads the whole
transaction to a worker thread with :func:`asyncio.to_thread` (the
blocking-io runtime gate in ``tests/blocking_io/`` enforces this).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from deerflow.config.app_config import EmailConfig

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    """Raised when outbound email is disabled or misconfigured."""


def _build_message(*, config: EmailConfig, to: str, subject: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{config.from_name or 'Nova'} <{config.from_email}>"
    msg["To"] = to
    msg.set_content("This is an HTML email. Please view it in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    return msg


def _send_smtp(*, config: EmailConfig, to: str, subject: str, html: str) -> None:
    """Synchronous SMTP send — must only run inside a worker thread."""
    message = _build_message(config=config, to=to, subject=subject, html=html)

    if config.use_ssl:
        server = smtplib.SMTP_SSL(config.host, config.port, timeout=30)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=30)
        server.ehlo()
        if config.use_tls:
            server.starttls()
            server.ehlo()

    try:
        if config.username:
            server.login(config.username, config.password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except smtplib.SMTPServerDisconnected:
            pass


async def send_email(*, config: EmailConfig, to: str, subject: str, html: str) -> None:
    """Send an HTML email through the configured SMTP server.

    Raises :class:`EmailNotConfiguredError` when email is disabled; any
    SMTP failure propagates as :class:`smtplib.SMTPException`.
    """
    if not config.enabled:
        raise EmailNotConfiguredError("Outbound email is not enabled")

    missing = [name for name, value in (("host", config.host), ("from_email", config.from_email)) if not value]
    if missing:
        raise EmailNotConfiguredError(f"SMTP missing required config: {', '.join(missing)}")

    await asyncio.to_thread(_send_smtp, config=config, to=to, subject=subject, html=html)
    logger.info("Sent email to %s", to)
