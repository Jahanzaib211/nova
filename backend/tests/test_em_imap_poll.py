"""IMAP bounce/complaint/unsubscribe polling with a fake IMAP4 client:
resumes from the stored UID cursor, resets on a UIDVALIDITY change, matches
each message to its send, records the event and suppresses."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.config.email_marketing_config import BounceMailboxConfig
from deerflow.email_marketing.imap import BouncePoller, fetch_new_messages
from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

pytestmark = pytest.mark.anyio


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL for the poller."""

    uidvalidity = b"7"
    messages: dict[int, bytes] = {}

    def __init__(self, host, port):
        self.logged_out = False

    def login(self, u, p):
        return "OK", [b""]

    def select(self, folder, readonly=True):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, key):
        return key, [self.uidvalidity]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            criteria = args[-1]
            start = int(criteria.split(":")[0].split()[-1])
            uids = [u for u in sorted(self.messages) if u >= start]
            return "OK", [" ".join(str(u) for u in uids).encode()]
        if cmd == "FETCH":
            uid = int(args[0])
            return "OK", [(f"{uid} (RFC822 {{{len(self.messages[uid])}}}".encode(), self.messages[uid]), b")"]
        raise AssertionError(cmd)

    def logout(self):
        self.logged_out = True


def _dsn(send_id: str, status: str = "5.1.1") -> bytes:
    b = "B"
    return "\n".join(
        [
            "From: MAILER-DAEMON@mail.example",
            f"To: bounce+{send_id}@x.y",
            "Subject: Undelivered",
            "MIME-Version: 1.0",
            f'Content-Type: multipart/report; report-type=delivery-status; boundary="{b}"',
            "",
            f"--{b}",
            "Content-Type: text/plain",
            "",
            "failed",
            f"--{b}",
            "Content-Type: message/delivery-status",
            "",
            "Reporting-MTA: dns; mail.example",
            "",
            "Final-Recipient: rfc822; gone@nowhere.example",
            "Action: failed",
            f"Status: {status}",
            "Diagnostic-Code: smtp; 550 5.1.1 User unknown",
            "",
            f"--{b}--",
            "",
        ]
    ).encode()


def _unsub(send_id: str) -> bytes:
    m = EmailMessage()
    m["From"] = "person@example.com"
    m["To"] = f"unsubscribe+{send_id}@x.y"
    m["Subject"] = "unsubscribe"
    m.set_content("please")
    return bytes(m)


@pytest.fixture()
def repo():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield EmailMarketingRepository(sf)
    asyncio.run(engine.dispose())


def test_fetch_new_messages_resumes_from_cursor_and_detects_uidvalidity_change():
    FakeIMAP.messages = {1: b"a", 2: b"b", 3: b"c"}
    FakeIMAP.uidvalidity = b"7"
    cfg = BounceMailboxConfig(host="h", username="u", password="p")
    got = fetch_new_messages(cfg, last_uid=1, uidvalidity=7, imap_factory=FakeIMAP)
    assert [u for u, _ in got.messages] == [2, 3] and got.uidvalidity == 7 and got.reset is False
    got2 = fetch_new_messages(cfg, last_uid=2, uidvalidity=6, imap_factory=FakeIMAP)  # mailbox rebuilt
    assert got2.reset is True and [u for u, _ in got2.messages] == [1, 2, 3]


async def test_poller_records_bounces_and_unsubscribes(repo):
    lst = await repo.create_list("alice", name="L")
    tpl = await repo.create_template("alice", name="T", subject="s", html="h")
    c1 = await repo.upsert_contact("alice", email="gone@nowhere.example")
    c2 = await repo.upsert_contact("alice", email="tired@example.com")
    await repo.add_members("alice", lst["id"], [c1["id"], c2["id"]])
    camp = await repo.create_campaign("alice", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="N")
    sends = await repo.create_sends("alice", camp["id"], await repo.recipients_for_list("alice", lst["id"]))
    by_email = {s["email"]: s for s in sends}
    FakeIMAP.messages = {10: _dsn(by_email["gone@nowhere.example"]["id"]), 11: _unsub(by_email["tired@example.com"]["id"]), 12: b"From: x@y\nSubject: lunch\n\nhi"}
    FakeIMAP.uidvalidity = b"7"
    poller = BouncePoller(repo, BounceMailboxConfig(host="h", username="u", password="p"), imap_factory=FakeIMAP)
    summary = await poller.poll("alice")
    assert summary == {"fetched": 3, "bounced_hard": 1, "bounced_soft": 0, "complained": 0, "unsubscribed": 1, "replied": 0, "unmatched": 1}
    assert await repo.is_suppressed("alice", "gone@nowhere.example")
    assert await repo.is_suppressed("alice", "tired@example.com")
    assert (await repo.get_contact("alice", c2["id"]))["status"] == "unsubscribed"
    types = sorted(e["type"] for e in await repo.list_events("alice", campaign_id=camp["id"]))
    assert types == ["bounced_hard", "suppressed", "suppressed", "unsubscribed"]
    cursor = await repo.get_bounce_cursor("alice")
    assert cursor["last_uid"] == 12 and cursor["uidvalidity"] == 7
    # Second poll: nothing new, cursor unchanged.
    assert (await poller.poll("alice"))["fetched"] == 0


async def test_poller_hands_human_replies_to_the_bridge(repo):
    lst = await repo.create_list("alice", name="L")
    tpl = await repo.create_template("alice", name="T", subject="s", html="h")
    c = await repo.upsert_contact("alice", email="fan@example.com")
    await repo.add_members("alice", lst["id"], [c["id"]])
    camp = await repo.create_campaign("alice", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="N")
    send = (await repo.create_sends("alice", camp["id"], await repo.recipients_for_list("alice", lst["id"])))[0]
    m = EmailMessage()
    m["From"] = "fan@example.com"
    m["To"] = f"reply+{send['id']}@x.y"
    m["Subject"] = "Re: hello"
    m.set_content("love it")
    FakeIMAP.messages = {1: bytes(m)}
    FakeIMAP.uidvalidity = b"7"
    handed: list[str] = []

    async def on_reply(s, raw):
        handed.append(s["id"])

    poller = BouncePoller(repo, BounceMailboxConfig(host="h", username="u", password="p"), imap_factory=FakeIMAP, on_reply=on_reply)
    summary = await poller.poll("alice")
    assert summary["replied"] == 1 and handed == [send["id"]]
    assert [e["type"] for e in await repo.list_events("alice", campaign_id=camp["id"])] == ["replied"]
