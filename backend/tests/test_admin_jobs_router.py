"""``/api/v1/admin/jobs`` — admin-only, reachable with the ops token, cross-user."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig, set_auth_config
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.job.sql import JobRepository

_TEST_SECRET = "test-secret-key-admin-jobs-min-32chars!!"
_PASSWORD = "Passw0rd!Passw0rd!"

pytestmark = pytest.mark.no_auto_user


@pytest.fixture()
def app(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    asyncio.run(init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path}/admin_jobs.db", sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield create_app()
    finally:
        asyncio.run(close_engine())


def _repo() -> JobRepository:
    from deerflow.persistence.engine import get_session_factory

    return JobRepository(get_session_factory())


def _wire(app):
    app.state.jobs_repo = _repo()


def _user_client(app) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/v1/auth/register", json={"email": "member@example.com", "password": _PASSWORD})
    assert r.status_code in (200, 201), r.text
    return c


def test_requires_admin(app, monkeypatch):
    _wire(app)
    monkeypatch.delenv("NOVA_OPS_TOKEN", raising=False)
    anon = TestClient(app).get("/api/v1/admin/jobs/summary")
    assert anon.status_code == 401
    member = _user_client(app).get("/api/v1/admin/jobs/summary")
    assert member.status_code == 403


def test_ops_token_sees_summary_workers_and_dead_letter(app, monkeypatch):
    _wire(app)
    monkeypatch.setenv("NOVA_OPS_TOKEN", "ops-token-test")
    repo = app.state.jobs_repo
    a = asyncio.run(JobQueue(repo).enqueue("x", {}, owner_user_id="u1", max_attempts=1))
    asyncio.run(JobQueue(repo).enqueue("y", {}, owner_user_id="u2"))
    asyncio.run(repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30)))
    asyncio.run(repo.schedule_retry(a, error="boom"))  # max_attempts=1 → dead letter
    asyncio.run(repo.upsert_worker(worker_id="w1", hostname="h", pid=1, queues=["default"], version="t", current_job_ids=[]))
    # Mutating requests need the double-submit CSRF pair even with the ops
    # token — nova-ops self-issues one per request, exactly like this.
    h = {"X-Nova-Ops-Token": "ops-token-test", "X-CSRF-Token": "pair"}
    c = TestClient(app, cookies={"csrf_token": "pair"})
    summary = c.get("/api/v1/admin/jobs/summary", headers=h).json()
    assert summary["queued"] == 1 and summary["dead_letter"] == 1
    assert summary["workers"][0]["worker_id"] == "w1" and summary["workers"][0]["last_seen_age_s"] < 5
    assert [j["id"] for j in c.get("/api/v1/admin/jobs/dead-letter", headers=h).json()["jobs"]] == [a]
    assert len(c.get("/api/v1/admin/jobs", headers=h).json()["jobs"]) == 2, "admin listing crosses users"
    assert c.post(f"/api/v1/admin/jobs/{a}/retry", headers=h).json() == {"ok": True, "status": "queued"}
    assert c.post(f"/api/v1/admin/jobs/{a}/retry", headers=h).status_code == 409
    assert c.post(f"/api/v1/admin/jobs/{a}/cancel", headers=h).json()["status"] == "cancelled"
    assert c.post("/api/v1/admin/jobs/missing/cancel", headers=h).status_code == 404
