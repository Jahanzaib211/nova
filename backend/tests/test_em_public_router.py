"""``/api/em/t/*`` and ``/api/em/u/*``: no session, token is the credential,
the click redirect never follows a URL whose HMAC does not match, one-click
unsubscribe works with a bare POST (no CSRF, no origin)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import _is_public as is_public_path
from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.routers import email_marketing_public
from deerflow.config.app_config import AppConfig
from deerflow.email_marketing.tracking import TrackingLinks, make_token
from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

pytestmark = pytest.mark.no_auto_user
SANDBOX = {"use": "deerflow.sandbox.local.local_sandbox:LocalSandboxProvider"}
SECRET = "k"


@pytest.fixture()
def world(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    em = EmailMarketingRepository(sf)
    cfg = AppConfig.model_validate({"sandbox": SANDBOX, "email_marketing": {"enabled": True, "public_base_url": "https://nova.example.com", "tracking_secret": SECRET}})
    monkeypatch.setattr(email_marketing_public, "get_config", lambda: cfg)

    async def _seed():
        lst = await em.create_list("alice", name="L")
        tpl = await em.create_template("alice", name="T", subject="s", html="h")
        c = await em.upsert_contact("alice", email="one@example.com")
        await em.add_members("alice", lst["id"], [c["id"]])
        camp = await em.create_campaign("alice", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="N")
        sends = await em.create_sends("alice", camp["id"], await em.recipients_for_list("alice", lst["id"]))
        return {"contact": c, "campaign": camp, "send": sends[0]}

    seeded = asyncio.run(_seed())
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.state.em_repo = em
    app.include_router(email_marketing_public.router)
    yield {"em": em, "client": TestClient(app), **seeded}
    asyncio.run(engine.dispose())


def test_paths_are_public_by_prefix():
    assert is_public_path("/api/em/t/o/abc.gif")
    assert is_public_path("/api/em/t/c/abc")
    assert is_public_path("/api/em/u/abc")
    assert not is_public_path("/api/em/lists")


def test_open_pixel_records_once_and_always_returns_a_gif(world):
    c, em, send = world["client"], world["em"], world["send"]
    links = TrackingLinks(base_url="https://nova.example.com", secret=SECRET, send_id=send["id"])
    path = links.open_pixel_url().split("nova.example.com", 1)[1]
    r = c.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif" and "no-store" in r.headers["cache-control"]
    c.get(path)
    events = asyncio.run(em.list_events("alice", campaign_id=world["campaign"]["id"], type="opened"))
    assert len(events) == 2  # every open recorded; stats count unique sends
    # A forged token still gets a gif (never leak validity), records nothing.
    assert c.get("/api/em/t/o/forged.gif").status_code == 200
    assert len(asyncio.run(em.list_events("alice", type="opened"))) == 2


def test_click_redirects_only_to_signed_urls(world):
    c, em, send = world["client"], world["em"], world["send"]
    links = TrackingLinks(base_url="https://nova.example.com", secret=SECRET, send_id=send["id"])
    path = links.click_url("https://shop.example/?a=1").split("nova.example.com", 1)[1]
    r = c.get(path, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://shop.example/?a=1"
    assert asyncio.run(em.list_events("alice", type="clicked"))[0]["payload"]["url"] == "https://shop.example/?a=1"
    tok = make_token(SECRET, "click", send["id"])
    evil = c.get(f"/api/em/t/c/{tok}?u=https%3A%2F%2Fevil.example%2F&sig=nope", follow_redirects=False)
    assert evil.status_code == 400
    assert c.get("/api/em/t/c/forged?u=https%3A%2F%2Fshop.example%2F&sig=x", follow_redirects=False).status_code == 404


def test_one_click_unsubscribe_without_csrf_or_origin(world):
    c, em, send, contact = world["client"], world["em"], world["send"], world["contact"]
    tok = make_token(SECRET, "unsub", send["id"])
    r = c.post(f"/api/em/u/{tok}", content="List-Unsubscribe=One-Click", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert asyncio.run(em.get_contact("alice", contact["id"]))["status"] == "unsubscribed"
    assert asyncio.run(em.is_suppressed("alice", "one@example.com"))
    page = c.get(f"/api/em/u/{tok}")
    assert page.status_code == 200 and "unsubscribed" in page.text.lower()
    assert c.post("/api/em/u/forged").status_code == 404
