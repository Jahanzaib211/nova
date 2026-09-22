"""Tracking endpoints sit on the request path with no session; they must not
block the loop (the SMTP/IMAP work lives in worker threads, not here)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.routers import email_marketing_public
from deerflow.config.app_config import AppConfig
from deerflow.email_marketing.tracking import TrackingLinks
from deerflow.persistence.base import Base
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

pytestmark = pytest.mark.anyio


async def test_open_and_click_do_no_blocking_io(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    em = EmailMarketingRepository(sf)
    cfg = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local.local_sandbox:LocalSandboxProvider"}, "email_marketing": {"enabled": True, "public_base_url": "https://n.example", "tracking_secret": "k"}})
    monkeypatch.setattr(email_marketing_public, "get_config", lambda: cfg)
    lst = await em.create_list("o", name="L")
    tpl = await em.create_template("o", name="T", subject="s", html="h")
    c = await em.upsert_contact("o", email="a@b.co")
    await em.add_members("o", lst["id"], [c["id"]])
    camp = await em.create_campaign("o", name="C", list_id=lst["id"], template_id=tpl["id"], from_email="n@x.y", from_name="N")
    send = (await em.create_sends("o", camp["id"], await em.recipients_for_list("o", lst["id"])))[0]
    app = FastAPI()
    app.state.em_repo = em
    app.include_router(email_marketing_public.router)
    links = TrackingLinks(base_url="https://n.example", secret="k", send_id=send["id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://n.example") as client:
        assert (await client.get(links.open_pixel_url())).status_code == 200
        r = await client.get(links.click_url("https://shop.example/"), follow_redirects=False)
        assert r.status_code == 302
    await engine.dispose()
