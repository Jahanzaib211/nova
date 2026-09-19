"""``/api/em`` — owner scoping and the campaign lifecycle actions."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import email_marketing
from deerflow.config.app_config import AppConfig
from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository
from deerflow.persistence.job.sql import JobRepository

pytestmark = pytest.mark.no_auto_user
SANDBOX = {"use": "deerflow.sandbox.local.local_sandbox:LocalSandboxProvider"}


def _user(email: str) -> User:
    return User(email=email, password_hash="x", system_role="user", id=uuid4())


@pytest.fixture()
def world(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    em, jobs = EmailMarketingRepository(sf), JobRepository(sf)
    cfg = AppConfig.model_validate({"sandbox": SANDBOX, "email_marketing": {"enabled": True, "public_base_url": "https://nova.example.com", "tracking_secret": "k"}, "email": {"enabled": True, "host": "relay", "from_email": "n@x.y"}})
    monkeypatch.setattr(email_marketing, "get_config", lambda: cfg)
    yield {"em": em, "jobs": jobs, "cfg": cfg}
    asyncio.run(engine.dispose())


def _client(world, user: User) -> TestClient:
    app = make_authed_test_app(user_factory=lambda: user)
    app.state.em_repo = world["em"]
    app.state.jobs_repo = world["jobs"]
    app.include_router(email_marketing.router)
    return TestClient(app)


def test_lists_contacts_templates_are_owner_scoped(world):
    alice, bob = _user("a@x.y"), _user("b@x.y")
    a, b = _client(world, alice), _client(world, bob)
    lst = a.post("/api/em/lists", json={"name": "News"}).json()
    assert a.get("/api/em/lists").json()["lists"][0]["id"] == lst["id"]
    assert b.get("/api/em/lists").json()["lists"] == []
    assert b.get(f"/api/em/lists/{lst['id']}").status_code == 404
    c = a.post("/api/em/contacts", json={"email": "Ada@Example.com", "first_name": "Ada"}).json()
    assert c["email"] == "Ada@Example.com"
    assert a.post(f"/api/em/lists/{lst['id']}/members", json={"contact_ids": [c["id"]]}).json()["added"] == 1
    assert b.post(f"/api/em/lists/{lst['id']}/members", json={"contact_ids": [c["id"]]}).status_code == 404
    assert a.get(f"/api/em/lists/{lst['id']}/members").json()["contacts"][0]["id"] == c["id"]
    tpl = a.post("/api/em/templates", json={"name": "T", "subject": "Hi {{ contact.first_name }}", "html": "<p>{{ contact.first_name }}</p>"}).json()
    assert b.get(f"/api/em/templates/{tpl['id']}").status_code == 404
    preview = a.post(f"/api/em/templates/{tpl['id']}/preview", json={"contact_id": c["id"]}).json()
    assert preview["subject"] == "Hi Ada" and "<p>Ada</p>" in preview["html"]
    bad = a.post("/api/em/templates", json={"name": "B", "subject": "s", "html": "{{ contact.first_name }"})
    assert bad.status_code == 422


def test_campaign_actions_and_preflight(world):
    alice = _user("a@x.y")
    a = _client(world, alice)
    lst = a.post("/api/em/lists", json={"name": "L"}).json()
    c = a.post("/api/em/contacts", json={"email": "one@example.com"}).json()
    a.post(f"/api/em/lists/{lst['id']}/members", json={"contact_ids": [c["id"]]})
    tpl = a.post("/api/em/templates", json={"name": "T", "subject": "s", "html": "<p>x</p>"}).json()
    camp = a.post("/api/em/campaigns", json={"name": "C", "list_id": lst["id"], "template_id": tpl["id"], "from_email": "n@x.y", "from_name": "N"}).json()
    assert camp["status"] == "draft"
    pre = a.get(f"/api/em/campaigns/{camp['id']}/preflight").json()
    assert pre["ok"] is True and pre["recipients"] == 1 and pre["suppressed"] == 0
    started = a.post(f"/api/em/campaigns/{camp['id']}/send-now").json()
    assert started["status"] == "sending" and started["job_id"]
    jobs = asyncio.run(world["jobs"].list_jobs(owner_user_id=str(alice.id)))
    assert [j["type"] for j in jobs] == ["em.campaign.start"]
    assert a.post(f"/api/em/campaigns/{camp['id']}/pause").json()["status"] == "paused"
    resumed = a.post(f"/api/em/campaigns/{camp['id']}/resume").json()
    assert resumed["status"] == "sending"
    assert a.post(f"/api/em/campaigns/{camp['id']}/cancel").json()["status"] == "cancelled"
    assert a.get(f"/api/em/campaigns/{camp['id']}/stats").json()["recipients"] == 0
    assert a.get(f"/api/em/campaigns/{camp['id']}/events").json()["events"] == []
    assert _client(world, _user("b@x.y")).post(f"/api/em/campaigns/{camp['id']}/send-now").status_code == 404


def test_send_now_refuses_when_not_ready(world, monkeypatch):
    alice = _user("a@x.y")
    a = _client(world, alice)
    lst = a.post("/api/em/lists", json={"name": "L"}).json()
    tpl = a.post("/api/em/templates", json={"name": "T", "subject": "s", "html": "x"}).json()
    camp = a.post("/api/em/campaigns", json={"name": "C", "list_id": lst["id"], "template_id": tpl["id"], "from_email": "n@x.y", "from_name": "N"}).json()
    empty = a.get(f"/api/em/campaigns/{camp['id']}/preflight").json()
    assert empty["ok"] is False and "no recipients" in " ".join(empty["problems"])
    assert a.post(f"/api/em/campaigns/{camp['id']}/send-now").status_code == 409
    cfg = AppConfig.model_validate({"sandbox": SANDBOX, "email_marketing": {"enabled": True}})
    monkeypatch.setattr(email_marketing, "get_config", lambda: cfg)
    pre = a.get(f"/api/em/campaigns/{camp['id']}/preflight").json()
    assert any("public_base_url" in p for p in pre["problems"])


def test_suppressions_and_import_job(world):
    alice = _user("a@x.y")
    a = _client(world, alice)
    s = a.post("/api/em/suppressions", json={"email": "Bad@Example.com", "reason": "manual"}).json()
    assert s["email"] == "bad@example.com"
    assert a.get("/api/em/suppressions").json()["suppressions"][0]["reason"] == "manual"
    assert a.delete("/api/em/suppressions/bad@example.com").json()["ok"] is True
    imp = a.post("/api/em/contacts/import", json={"csv_text": "email,first\na@b.co,A\n", "mapping": {"email": "email", "first_name": "first"}}).json()
    assert imp["job_id"] and imp["preview"] == {"total": 1, "valid": 1, "duplicates": 0, "invalid": 0}
    guess = a.post("/api/em/contacts/import/mapping", json={"csv_text": "E-mail,Surname\n"}).json()
    assert guess["mapping"] == {"email": "E-mail", "last_name": "Surname"}


def test_disabled_feature_is_404(world, monkeypatch):
    monkeypatch.setattr(email_marketing, "get_config", lambda: AppConfig.model_validate({"sandbox": SANDBOX, "email_marketing": {"enabled": False}}))
    assert _client(world, _user("a@x.y")).get("/api/em/lists").status_code == 404
