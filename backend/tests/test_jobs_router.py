"""``/api/jobs`` — owner scoping, actions, schedules, SSE."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import jobs
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.base import Base
from deerflow.persistence.job.sql import JobRepository

pytestmark = pytest.mark.no_auto_user


def _user(email: str) -> User:
    return User(email=email, password_hash="x", system_role="user", id=uuid4())


@pytest.fixture()
def repo():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield JobRepository(sf)
    asyncio.run(engine.dispose())


def _client(repo: JobRepository, user: User) -> TestClient:
    app = make_authed_test_app(user_factory=lambda: user)
    app.state.jobs_repo = repo
    app.include_router(jobs.router)
    return TestClient(app)


def _enqueue(repo, **kw) -> str:
    return asyncio.run(JobQueue(repo).enqueue(kw.pop("type", "demo"), kw.pop("payload", {}), **kw))


def test_list_and_get_are_owner_scoped(repo):
    alice, bob = _user("a@example.com"), _user("b@example.com")
    mine = _enqueue(repo, owner_user_id=str(alice.id))
    theirs = _enqueue(repo, owner_user_id=str(bob.id))
    c = _client(repo, alice)
    listed = c.get("/api/jobs").json()["jobs"]
    assert [j["id"] for j in listed] == [mine]
    assert c.get(f"/api/jobs/{mine}").status_code == 200
    assert c.get(f"/api/jobs/{theirs}").status_code == 404, "someone else's job is indistinguishable from a missing one"
    assert c.get("/api/jobs?status=bogus").status_code == 422


def test_cancel_queued_settles_immediately_and_running_is_cooperative(repo):
    alice = _user("a@example.com")
    queued = _enqueue(repo, owner_user_id=str(alice.id))
    running = _enqueue(repo, owner_user_id=str(alice.id))
    asyncio.run(repo.claim(queues=["default"], worker_id="w", lease_ttl=timedelta(seconds=30), limit=2))
    asyncio.run(repo.mark_running(running, worker_id="w"))
    # `queued` was claimed too; put it back to queued for the test
    asyncio.run(repo.release(queued, worker_id="w"))
    c = _client(repo, alice)
    assert c.post(f"/api/jobs/{queued}/cancel").json() == {"ok": True, "status": "cancelled"}
    assert c.post(f"/api/jobs/{running}/cancel").json() == {"ok": True, "status": "running"}
    assert asyncio.run(repo.get(running))["cancel_requested"] is True
    assert c.post(f"/api/jobs/{queued}/cancel").status_code == 409


def test_retry_only_for_failed_or_dead_letter(repo):
    alice = _user("a@example.com")
    job = _enqueue(repo, owner_user_id=str(alice.id))
    c = _client(repo, alice)
    assert c.post(f"/api/jobs/{job}/retry").status_code == 409
    asyncio.run(repo.claim(queues=["default"], worker_id="w", lease_ttl=timedelta(seconds=30)))
    asyncio.run(repo.mark_failed(job, error="boom"))
    assert c.post(f"/api/jobs/{job}/retry").json() == {"ok": True, "status": "queued"}


def test_events_poll_and_sse_stream(repo):
    alice = _user("a@example.com")
    job = _enqueue(repo, owner_user_id=str(alice.id))
    asyncio.run(repo.claim(queues=["default"], worker_id="w", lease_ttl=timedelta(seconds=30)))
    asyncio.run(repo.progress(job, pct=50, message="half"))
    asyncio.run(repo.mark_succeeded(job, result={"n": 1}))
    c = _client(repo, alice)
    body = c.get(f"/api/jobs/{job}/events?since_seq=1").json()
    assert body["status"] == "succeeded"
    assert [e["type"] for e in body["events"]] == ["leased", "progress", "succeeded"]
    with c.stream("GET", f"/api/jobs/{job}/events/stream") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())
    frames = [f for f in text.split("\n\n") if f.startswith("event:")]
    assert frames[0].startswith("event: job_event")
    assert frames[-1].startswith("event: job_done")
    assert json.loads(frames[-1].split("data: ", 1)[1]) == {"status": "succeeded"}


def test_schedules_crud_is_owner_scoped_and_validates_cron(repo):
    alice, bob = _user("a@example.com"), _user("b@example.com")
    c = _client(repo, alice)
    bad = c.post("/api/jobs/schedules", json={"name": "n", "type": "demo", "cron": "not cron"})
    assert bad.status_code == 422
    created = c.post("/api/jobs/schedules", json={"name": "nightly", "type": "demo", "cron": "0 3 * * *", "timezone": "Asia/Karachi", "payload": {"k": 1}})
    assert created.status_code == 201
    sid = created.json()["id"]
    assert created.json()["next_run_at"] is not None
    assert [s["id"] for s in c.get("/api/jobs/schedules").json()["schedules"]] == [sid]
    # bob cannot see, edit, delete or take the name
    b = _client(repo, bob)
    assert b.get("/api/jobs/schedules").json()["schedules"] == []
    assert b.patch(f"/api/jobs/schedules/{sid}", json={"enabled": False}).status_code == 404
    assert b.delete(f"/api/jobs/schedules/{sid}").status_code == 404
    assert b.post("/api/jobs/schedules", json={"name": "nightly", "type": "demo", "cron": "* * * * *"}).status_code == 409
    updated = c.patch(f"/api/jobs/schedules/{sid}", json={"enabled": False, "cron": "*/10 * * * *"}).json()
    assert updated["enabled"] is False and updated["cron"] == "*/10 * * * *"
    assert c.delete(f"/api/jobs/schedules/{sid}").json()["ok"] is True
    # a literal "schedules" path never resolves as a job id
    assert c.get("/api/jobs/schedules").status_code == 200
