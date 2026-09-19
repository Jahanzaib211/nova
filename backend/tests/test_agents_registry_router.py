"""``/api/agents/registry`` — lead + built-in subagents + custom + ACP with live counts."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import agents_registry
from deerflow.config.app_config import AppConfig
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.base import Base
from deerflow.persistence.job.sql import JobRepository

pytestmark = pytest.mark.no_auto_user
SANDBOX = {"use": "deerflow.sandbox.local.local_sandbox:LocalSandboxProvider"}


@pytest.fixture()
def repo():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield JobRepository(sf)
    asyncio.run(engine.dispose())


def test_registry_lists_every_kind_with_counts(repo, monkeypatch):
    user = User(email="a@x.y", password_hash="x", system_role="user", id=uuid4())
    cfg = AppConfig.model_validate(
        {
            "sandbox": SANDBOX,
            "jobs": {"enabled": True},
            "subagents": {"async_enabled": True, "custom_agents": {"researcher": {"description": "digs", "system_prompt": "x"}}},
            "acp_agents": {"claude_code": {"command": "npx", "description": "Claude Code"}},
        }
    )
    monkeypatch.setattr(agents_registry, "get_config", lambda: cfg)
    monkeypatch.setattr("deerflow.config.agents_config.list_custom_agents", lambda *, user_id=None: [])
    asyncio.run(JobQueue(repo).enqueue("agents.task", {"agent": "general-purpose", "kind": "subagent", "task": "t"}, owner_user_id=str(user.id)))
    asyncio.run(JobQueue(repo).enqueue("agents.task", {"agent": "claude_code", "kind": "acp", "task": "t"}, owner_user_id="someone-else"))
    app = make_authed_test_app(user_factory=lambda: user)
    app.state.jobs_repo = repo
    app.include_router(agents_registry.router)
    body = TestClient(app).get("/api/agents/registry").json()
    assert body["async_enabled"] is True
    by_id = {a["id"]: a for a in body["agents"]}
    assert by_id["lead"]["kind"] == "lead"
    assert by_id["subagent:general-purpose"]["queued"] == 1 and by_id["subagent:general-purpose"]["runner"] == "jobs"
    assert by_id["subagent:researcher"]["description"] == "digs"
    assert by_id["acp:claude_code"]["queued"] == 0  # other owner's job is not ours
    kinds = {a["kind"] for a in body["agents"]}
    assert kinds == {"lead", "subagent", "acp"}


def test_registry_without_async_marks_gateway_runner(repo, monkeypatch):
    user = User(email="a@x.y", password_hash="x", system_role="user", id=uuid4())
    cfg = AppConfig.model_validate({"sandbox": SANDBOX})
    monkeypatch.setattr(agents_registry, "get_config", lambda: cfg)
    monkeypatch.setattr("deerflow.config.agents_config.list_custom_agents", lambda *, user_id=None: [])
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(agents_registry.router)
    body = TestClient(app).get("/api/agents/registry").json()
    assert body["async_enabled"] is False
    assert all(a["runner"] == "gateway" for a in body["agents"])
