"""The campaign pipeline end to end on SQLite with a fake SMTP: start
snapshots recipients minus suppressions and fans out batches; a batch
renders per contact, sends, records events, suppresses hard bounces and
requeues throttled/deferred sends; pause/cancel stop the flow."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.config.email_config import EmailConfig
from deerflow.config.email_marketing_config import EmailMarketingConfig
from deerflow.email_marketing.pipeline import CampaignPipeline, PipelineDeps
from deerflow.email_marketing.smtp import OutboundMessage, SendResult
from deerflow.jobs.context import JobContext
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository
from deerflow.persistence.job.sql import JobRepository

pytestmark = pytest.mark.anyio


class FakeSender:
    def __init__(self) -> None:
        self.batches: list[list[OutboundMessage]] = []
        self.script: dict[str, str] = {}

    def send_batch(self, messages: list[OutboundMessage]) -> list[SendResult]:
        self.batches.append(messages)
        out = []
        for m in messages:
            status = self.script.get(m.to, "sent")
            out.append(SendResult(m.send_id, status, message_id=f"<{m.send_id}@x.y>" if status == "sent" else None, error=None if status == "sent" else "550 5.1.1 no such user"))
        return out


@pytest.fixture()
def world():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    em = EmailMarketingRepository(sf)
    jobs = JobRepository(sf)
    sender = FakeSender()
    cfg = EmailMarketingConfig(enabled=True, public_base_url="https://nova.example.com", tracking_secret="k", batch_size=2, default_throttle_per_minute=600)
    deps = PipelineDeps(em=em, queue=JobQueue(jobs), config=cfg, email=EmailConfig(enabled=True, host="relay", port=25, from_email="n@x.y", from_name="Nova"), sender_factory=lambda **kw: sender)
    yield {"em": em, "jobs": jobs, "sender": sender, "pipeline": CampaignPipeline(deps), "sf": sf}
    asyncio.run(engine.dispose())


async def _ctx(jobs: JobRepository, job_type: str, payload: dict[str, Any], owner: str) -> JobContext:
    job_id = await JobQueue(jobs).enqueue(job_type, payload, owner_user_id=owner)
    job = await jobs.get(job_id)
    return JobContext(jobs, job, worker_id="t", lease_ttl=timedelta(seconds=60))


async def _seed(em: EmailMarketingRepository, n: int = 3) -> dict[str, Any]:
    lst = await em.create_list("alice", name="L")
    tpl = await em.create_template("alice", name="T", subject="Hi {{ contact.first_name }}", html='<p>Hello {{ contact.first_name }}</p><a href="https://shop.example/">shop</a>')
    ids = []
    for i in range(n):
        c = await em.upsert_contact("alice", email=f"u{i}@example.com", first_name=f"U{i}")
        ids.append(c["id"])
    await em.add_members("alice", lst["id"], ids)
    camp = await em.create_campaign("alice", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="Nova")
    return {"list": lst, "template": tpl, "campaign": camp}


async def test_start_snapshots_recipients_and_fans_out_batches(world):
    em, jobs, pipeline = world["em"], world["jobs"], world["pipeline"]
    seeded = await _seed(em, 3)
    await em.suppress("alice", "u1@example.com", reason="manual")
    camp = seeded["campaign"]
    ctx = await _ctx(jobs, "em.campaign.start", {"campaign_id": camp["id"]}, "alice")
    result = await pipeline.start(ctx)
    assert result["recipients"] == 2 and result["batches"] == 1
    assert (await em.get_campaign("alice", camp["id"]))["status"] == "sending"
    queued = [j for j in await jobs.list_jobs(owner_user_id="alice") if j["type"] == "em.campaign.batch"]
    assert len(queued) == 1
    assert queued[0]["payload"]["campaign_id"] == camp["id"]


async def test_batch_renders_sends_records_and_suppresses(world):
    em, jobs, pipeline, sender = world["em"], world["jobs"], world["pipeline"], world["sender"]
    seeded = await _seed(em, 2)
    camp = seeded["campaign"]
    await pipeline.start(await _ctx(jobs, "em.campaign.start", {"campaign_id": camp["id"]}, "alice"))
    sender.script["u1@example.com"] = "bounced_hard"
    result = await pipeline.batch(await _ctx(jobs, "em.campaign.batch", {"campaign_id": camp["id"]}, "alice"))
    assert result["sent"] == 1 and result["failed"] == 1
    msgs = sender.batches[0]
    assert msgs[0].subject == "Hi U0" and "Hello U0" in msgs[0].html
    assert "/api/em/t/c/" in msgs[0].html and "/api/em/t/o/" in msgs[0].html
    assert msgs[0].unsubscribe_url and "/api/em/u/" in msgs[0].unsubscribe_url
    assert await em.is_suppressed("alice", "u1@example.com")
    types = sorted(e["type"] for e in await em.list_events("alice", campaign_id=camp["id"]))
    assert types == ["bounced_hard", "queued", "queued", "sent", "suppressed"]
    camp_after = await em.get_campaign("alice", camp["id"])
    assert camp_after["status"] == "completed"
    assert camp_after["stats"]["sent"] == 1 and camp_after["stats"]["bounced_hard"] == 1


async def test_throttled_and_deferred_are_requeued_for_a_later_batch(world):
    em, jobs, pipeline, sender = world["em"], world["jobs"], world["pipeline"], world["sender"]
    seeded = await _seed(em, 2)
    camp = seeded["campaign"]
    await pipeline.start(await _ctx(jobs, "em.campaign.start", {"campaign_id": camp["id"]}, "alice"))
    sender.script["u1@example.com"] = "throttled"
    ctx = await _ctx(jobs, "em.campaign.batch", {"campaign_id": camp["id"]}, "alice")
    before = len([j for j in await jobs.list_jobs(owner_user_id="alice") if j["type"] == "em.campaign.batch"])
    result = await pipeline.batch(ctx)
    assert result["sent"] == 1 and result["requeued"] == 1
    assert (await em.get_campaign("alice", camp["id"]))["status"] == "sending"
    after = [j for j in await jobs.list_jobs(owner_user_id="alice") if j["type"] == "em.campaign.batch"]
    assert len(after) == before + 1  # exactly one follow-up batch, spaced by the throttle
    newest = max(after, key=lambda j: j["created_at"])
    assert newest["run_after"] > newest["created_at"]
    counts = await em.send_counts(camp["id"])
    assert counts == {"sent": 1, "queued": 1}


async def test_paused_or_cancelled_campaign_stops_the_batch(world):
    em, jobs, pipeline, sender = world["em"], world["jobs"], world["pipeline"], world["sender"]
    seeded = await _seed(em, 2)
    camp = seeded["campaign"]
    await pipeline.start(await _ctx(jobs, "em.campaign.start", {"campaign_id": camp["id"]}, "alice"))
    await em.update_campaign_status("alice", camp["id"], "paused")
    result = await pipeline.batch(await _ctx(jobs, "em.campaign.batch", {"campaign_id": camp["id"]}, "alice"))
    assert result["skipped"] == "paused"
    assert sender.batches == []
    assert (await em.send_counts(camp["id"])) == {"queued": 2}


async def test_start_refuses_when_not_send_ready(world):
    em, jobs = world["em"], world["jobs"]
    seeded = await _seed(em, 1)
    bad = CampaignPipeline(PipelineDeps(em=em, queue=JobQueue(jobs), config=EmailMarketingConfig(enabled=True), email=EmailConfig(enabled=True, host="relay", from_email="n@x.y"), sender_factory=lambda **kw: FakeSender()))
    ctx = await _ctx(jobs, "em.campaign.start", {"campaign_id": seeded["campaign"]["id"]}, "alice")
    with pytest.raises(RuntimeError, match="public_base_url"):
        await bad.start(ctx)
    assert (await em.get_campaign("alice", seeded["campaign"]["id"]))["status"] == "failed"
