"""The SMTP sender against a fake server: one connection per batch, RSET
between messages, reconnect after a dropped connection, 4xx → deferred,
5xx → failed (hard/soft by code), per-domain throttle → reschedule."""

from __future__ import annotations

import smtplib

import pytest

from deerflow.email_marketing.smtp import DomainThrottle, OutboundMessage, SmtpSender, build_message


class FakeSMTP:
    instances: list[FakeSMTP] = []
    script: dict[str, object] = {}

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.sent: list[tuple[str, list[str]]] = []
        self.rsets = 0
        self.quit_called = False
        self.alive = True
        FakeSMTP.instances.append(self)

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        self.login_args = (u, p)

    def rset(self):
        self.rsets += 1

    def sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
        behaviour = FakeSMTP.script.get(to_addrs[0])
        if behaviour == "drop":
            self.alive = False
            raise smtplib.SMTPServerDisconnected("connection dropped")
        if isinstance(behaviour, tuple):
            code, text = behaviour
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (code, text.encode())})
        self.sent.append((from_addr, list(to_addrs)))
        return {}

    def quit(self):
        self.quit_called = True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances = []
    FakeSMTP.script = {}


def _msg(i: int, to: str | None = None) -> OutboundMessage:
    return OutboundMessage(send_id=f"s{i}", to=to or f"user{i}@example.com", subject="hi", html="<p>hi</p>", text="hi")


def _sender(**kw):
    return SmtpSender(host="relay", port=25, use_tls=False, use_ssl=False, username="", password="", from_email="n@x.y", from_name="Nova", bounce_domain="x.y", smtp_factory=FakeSMTP, **kw)


def test_build_message_headers():
    raw = build_message(_msg(1), from_email="n@x.y", from_name="Nova", reply_to="r@x.y", message_id="<s1@x.y>", unsubscribe_url="https://n/u/t", unsubscribe_mailto="mailto:unsubscribe+s1@x.y")
    text = raw.decode()
    assert "Message-ID: <s1@x.y>" in text
    assert "List-Unsubscribe: <https://n/u/t>, <mailto:unsubscribe+s1@x.y>" in text
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in text
    assert "Precedence: bulk" in text
    assert "X-Nova-Send: s1" in text
    assert "Reply-To: r@x.y" in text
    assert "From: Nova <n@x.y>" in text


def test_one_connection_rset_between_and_verp_envelope():
    sender = _sender()
    results = sender.send_batch([_msg(1), _msg(2), _msg(3)])
    assert [r.status for r in results] == ["sent", "sent", "sent"]
    assert len(FakeSMTP.instances) == 1
    smtp = FakeSMTP.instances[0]
    assert smtp.rsets >= 2
    assert smtp.sent[0][0] == "bounce+s1@x.y"  # VERP envelope sender
    assert smtp.quit_called
    assert all(r.message_id and r.message_id.endswith("@x.y>") for r in results)


def test_reconnects_after_a_dropped_connection():
    FakeSMTP.script["user2@example.com"] = "drop"
    results = _sender().send_batch([_msg(1), _msg(2), _msg(3)])
    assert [r.status for r in results] == ["sent", "deferred", "sent"]
    assert len(FakeSMTP.instances) == 2  # reconnected once


def test_reply_codes_map_to_statuses():
    FakeSMTP.script["a@example.com"] = (451, "4.7.1 try later")
    FakeSMTP.script["b@example.com"] = (550, "5.1.1 user unknown")
    FakeSMTP.script["c@example.com"] = (552, "5.2.2 mailbox full")
    results = _sender().send_batch([_msg(1, "a@example.com"), _msg(2, "b@example.com"), _msg(3, "c@example.com")])
    assert [r.status for r in results] == ["deferred", "bounced_hard", "bounced_soft"]
    assert "user unknown" in results[1].error


def test_domain_throttle_defers_the_excess_without_sleeping():
    throttle = DomainThrottle(per_minute=2)
    msgs = [_msg(i, f"u{i}@gmail.com") for i in range(4)] + [_msg(9, "z@other.net")]
    results = _sender(throttle=throttle).send_batch(msgs)
    assert [r.status for r in results] == ["sent", "sent", "throttled", "throttled", "sent"]
