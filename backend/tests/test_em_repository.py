"""EmailMarketingRepository: owner scoping, dedupe by normalised email,
recipient snapshot minus suppressions, event recording and campaign stats."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

pytestmark = pytest.mark.anyio


@pytest.fixture()
def repo():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield EmailMarketingRepository(sf)
    asyncio.run(engine.dispose())


async def test_contacts_dedupe_on_normalised_email_and_are_owner_scoped(repo):
    a = await repo.upsert_contact("alice", email="  Ada@Example.COM ", first_name="Ada")
    again = await repo.upsert_contact("alice", email="ada@example.com", last_name="Lovelace")
    assert again["id"] == a["id"]
    assert again["first_name"] == "Ada" and again["last_name"] == "Lovelace"
    other = await repo.upsert_contact("bob", email="ada@example.com")
    assert other["id"] != a["id"]
    assert [c["id"] for c in (await repo.list_contacts("alice"))["contacts"]] == [a["id"]]
    assert await repo.get_contact("bob", a["id"]) is None


async def test_lists_members_and_recipient_snapshot_minus_suppressions(repo):
    lst = await repo.create_list("alice", name="Newsletter")
    c1 = await repo.upsert_contact("alice", email="one@example.com")
    c2 = await repo.upsert_contact("alice", email="two@example.com")
    c3 = await repo.upsert_contact("alice", email="three@example.com", status="unsubscribed")
    await repo.add_members("alice", lst["id"], [c1["id"], c2["id"], c3["id"]])
    await repo.add_members("alice", lst["id"], [c1["id"]])  # idempotent
    assert (await repo.get_list("alice", lst["id"]))["member_count"] == 3
    await repo.suppress("alice", "two@example.com", reason="hard_bounce", detail="550")
    recipients = await repo.recipients_for_list("alice", lst["id"])
    assert [r["email"] for r in recipients] == ["one@example.com"]
    assert await repo.get_list("bob", lst["id"]) is None


async def test_campaign_lifecycle_sends_events_and_stats(repo):
    lst = await repo.create_list("alice", name="L")
    tpl = await repo.create_template("alice", name="T", subject="Hi {{ contact.first_name }}", html="<p>x</p>")
    c1 = await repo.upsert_contact("alice", email="one@example.com")
    c2 = await repo.upsert_contact("alice", email="two@example.com")
    await repo.add_members("alice", lst["id"], [c1["id"], c2["id"]])
    camp = await repo.create_campaign("alice", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="N")
    assert camp["status"] == "draft"
    sends = await repo.create_sends("alice", camp["id"], await repo.recipients_for_list("alice", lst["id"]))
    assert len(sends) == 2 and all(s["status"] == "queued" for s in sends)
    await repo.update_campaign_status("alice", camp["id"], "sending", job_id="j1")
    batch = await repo.claim_queued_sends(camp["id"], limit=1)
    assert len(batch) == 1
    await repo.mark_send(batch[0]["id"], status="sent", message_id="<m1@x.y>")
    await repo.record_event("alice", type="sent", campaign_id=camp["id"], send_id=batch[0]["id"], contact_id=batch[0]["contact_id"])
    await repo.record_event("alice", type="opened", campaign_id=camp["id"], send_id=batch[0]["id"], contact_id=batch[0]["contact_id"])
    # An open recorded twice for one send counts once in unique stats.
    await repo.record_event("alice", type="opened", campaign_id=camp["id"], send_id=batch[0]["id"], contact_id=batch[0]["contact_id"])
    stats = await repo.campaign_stats("alice", camp["id"])
    assert stats["recipients"] == 2 and stats["sent"] == 1 and stats["queued"] == 1
    assert stats["opened"] == 1
    assert await repo.get_send(batch[0]["id"]) is not None
    assert (await repo.find_send_by_message_id("<m1@x.y>"))["id"] == batch[0]["id"]
    events = await repo.list_events("alice", campaign_id=camp["id"])
    assert [e["type"] for e in events] == ["opened", "opened", "sent"]  # newest first
    assert await repo.campaign_stats("bob", camp["id"]) is None


async def test_unsubscribe_marks_contact_and_suppresses(repo):
    c = await repo.upsert_contact("alice", email="bye@example.com")
    await repo.unsubscribe_contact("alice", c["id"], detail="one-click")
    assert (await repo.get_contact("alice", c["id"]))["status"] == "unsubscribed"
    supp = await repo.list_suppressions("alice")
    assert [s["email"] for s in supp] == ["bye@example.com"] and supp[0]["reason"] == "unsubscribe"
    assert await repo.is_suppressed("alice", "BYE@example.com")


async def test_bounce_cursor_round_trip(repo):
    assert await repo.get_bounce_cursor("alice") is None
    await repo.set_bounce_cursor("alice", mailbox="bounces@x.y", last_uid=41, uidvalidity=7)
    cur = await repo.get_bounce_cursor("alice")
    assert cur["last_uid"] == 41 and cur["uidvalidity"] == 7
    await repo.set_bounce_cursor("alice", mailbox="bounces@x.y", last_uid=50, uidvalidity=7, error="oops")
    assert (await repo.get_bounce_cursor("alice"))["last_error"] == "oops"
