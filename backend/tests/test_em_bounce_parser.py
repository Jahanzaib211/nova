"""DSN (RFC 3464) and ARF (RFC 5965) parsing, and matching a bounce back to
the send that caused it via VERP or the X-Nova-Send header."""

from __future__ import annotations

from email.message import EmailMessage

from deerflow.email_marketing.bounces import classify_bounce, parse_bounce, send_id_from_message


def _dsn(status: str, action: str = "failed", original_headers: str = "") -> bytes:
    """A Postfix-style DSN, byte for byte like the real thing."""
    boundary = "B1"
    parts = [
        "From: MAILER-DAEMON@mail.example",
        "To: bounce+send-42@cloud.example.com",
        "Subject: Undelivered Mail Returned to Sender",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/report; report-type=delivery-status; boundary="{boundary}"',
        "",
        f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "This is the mail system at host mail.example.",
        "",
        f"--{boundary}",
        "Content-Type: message/delivery-status",
        "",
        "Reporting-MTA: dns; mail.example",
        "",
        "Final-Recipient: rfc822; nobody@nowhere.example",
        f"Action: {action}",
        f"Status: {status}",
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown",
        "",
    ]
    if original_headers:
        parts += [f"--{boundary}", "Content-Type: text/rfc822-headers", "", original_headers, ""]
    parts += [f"--{boundary}--", ""]
    return "\n".join(parts).encode()


def test_hard_bounce_from_dsn_and_verp():
    raw = _dsn("5.1.1")
    parsed = parse_bounce(raw)
    assert parsed.kind == "bounced_hard"
    assert parsed.status == "5.1.1"
    assert parsed.recipient == "nobody@nowhere.example"
    assert "User unknown" in parsed.diagnostic
    assert send_id_from_message(raw) == "send-42"


def test_soft_bounce_from_4xx_and_delayed_action():
    assert parse_bounce(_dsn("4.2.2")).kind == "bounced_soft"
    assert parse_bounce(_dsn("5.0.0", action="delayed")).kind == "bounced_soft"


def test_send_id_from_x_nova_send_header_when_no_verp():
    msg = EmailMessage()
    msg["To"] = "bounces@cloud.example.com"
    msg["Subject"] = "failure notice"
    msg.set_content("Hi. This is the qmail-send program.\n\n--- Below this line is a copy of the message.\n\nX-Nova-Send: send-7\nSubject: hello\n")
    assert send_id_from_message(bytes(msg)) == "send-7"


def test_arf_complaint():
    msg = EmailMessage()
    msg["From"] = "abuse@isp.example"
    msg["To"] = "bounce+send-9@cloud.example.com"
    msg["Subject"] = "complaint"
    msg.set_content("An email abuse report.")
    msg.make_mixed()
    report = EmailMessage()
    report.set_content("Feedback-Type: abuse\nUser-Agent: Foo/1.0\nVersion: 1\nOriginal-Mail-From: <bounce+send-9@cloud.example.com>\n", subtype="feedback-report")
    report.set_type("message/feedback-report")
    msg.attach(report)
    msg.set_type("multipart/report")
    msg.set_param("report-type", "feedback-report")
    parsed = parse_bounce(bytes(msg))
    assert parsed.kind == "complained"
    assert send_id_from_message(bytes(msg)) == "send-9"


def test_unrelated_mail_is_not_a_bounce():
    msg = EmailMessage()
    msg["From"] = "friend@example.com"
    msg["To"] = "bounces@cloud.example.com"
    msg["Subject"] = "lunch?"
    msg.set_content("are you free?")
    assert parse_bounce(bytes(msg)).kind is None
    assert send_id_from_message(bytes(msg)) is None


def test_classify_smtp_reply_codes():
    assert classify_bounce(550, "5.1.1 user unknown") == "bounced_hard"
    assert classify_bounce(552, "mailbox full") == "bounced_soft"
    assert classify_bounce(451, "try later") == "deferred"
    assert classify_bounce(250, "ok") is None
