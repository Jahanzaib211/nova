"""delegate_async / check_delegation: gated by config, enqueue agents.task,
report status and result; agents.task job runs ACP/subagent and writes the
artifact."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.config.app_config import AppConfig
from deerflow.jobs.context import JobContext
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.base import Base
from deerflow.persistence.job.sql import JobRepository
from deerflow.tools.builtins.delegate_async_tool import build_delegate_tools

pytestmark = pytest.mark.anyio
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


async def test_delegate_enqueues_and_check_reports(repo):
    cfg = AppConfig.model_validate({"sandbox": SANDBOX, "jobs": {"enabled": True}, "subagents": {"async_enabled": True}, "acp_agents": {"claude_code": {"command": "npx", "description": "cc"}}})
    delegate, check = build_delegate_tools(cfg, lambda: repo)
    assert "claude_code (acp)" in delegate.description and "general-purpose (subagent)" in delegate.description
    msg = await delegate.coroutine(agent="claude_code", task="say hi", config_={"configurable": {"thread_id": "thr-1"}})
    assert msg.startswith("Delegated to claude_code as job ")
    job_id = msg.split("job ")[1].split(".")[0]
    job = await repo.get(job_id)
    assert job["type"] == "agents.task" and job["queue"] == "agents" and job["thread_id"] == "thr-1"
    assert job["payload"] == {"agent": "claude_code", "kind": "acp", "task": "say hi"}
    assert (await check.coroutine(job_id=job_id)).startswith("Status: queued")
    unknown = await delegate.coroutine(agent="nope", task="x")
    assert unknown.startswith("Error: unknown agent")
    assert (await check.coroutine(job_id="missing")).startswith("Error")


def test_tools_are_gated_by_config():
    from deerflow.tools import get_available_tools

    off = AppConfig.model_validate({"sandbox": SANDBOX, "jobs": {"enabled": True}, "subagents": {"async_enabled": False}})
    names = {t.name for t in get_available_tools(subagent_enabled=True, include_mcp=False, app_config=off)}
    assert "delegate_async" not in names
    on = AppConfig.model_validate({"sandbox": SANDBOX, "jobs": {"enabled": True}, "subagents": {"async_enabled": True}})
    names = {t.name for t in get_available_tools(subagent_enabled=True, include_mcp=False, app_config=on)}
    assert {"delegate_async", "check_delegation"} <= names
    # Never for subagents themselves (no nesting).
    names = {t.name for t in get_available_tools(subagent_enabled=False, include_mcp=False, app_config=on)}
    assert "delegate_async" not in names


async def test_agent_task_job_runs_acp_and_writes_artifact(repo, monkeypatch, tmp_path):
    from app.jobs.handlers import agents as handler
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "get_paths", lambda: paths_module.Paths(base_dir=tmp_path))
    fake_tool = SimpleNamespace(coroutine=lambda **kw: asyncio.sleep(0, result=f"done: {kw['prompt']}"))
    monkeypatch.setattr("deerflow.tools.builtins.invoke_acp_agent_tool.build_invoke_acp_agent_tool", lambda agents: fake_tool)
    monkeypatch.setattr("deerflow.config.acp_config.get_acp_agents", lambda: {"claude_code": SimpleNamespace(command="npx", description="cc")})
    job_id = await JobQueue(repo).enqueue("agents.task", {"agent": "claude_code", "kind": "acp", "task": "list files"}, owner_user_id="u1", thread_id="00000000-0000-0000-0000-000000000001")
    ctx = JobContext(repo, await repo.get(job_id), worker_id="t", lease_ttl=timedelta(seconds=60))
    result = await handler.agent_task(ctx)
    assert result["result"] == "done: list files"
    assert result["artifact"] and result["artifact"].endswith(f"{job_id}.md")
    written = list(tmp_path.rglob(f"{job_id}.md"))
    assert written and "done: list files" in written[0].read_text()


async def test_agent_task_job_rejects_unknown_kind_or_agent(repo, monkeypatch):
    from app.jobs.handlers import agents as handler

    monkeypatch.setattr("deerflow.config.acp_config.get_acp_agents", lambda: {})
    job_id = await JobQueue(repo).enqueue("agents.task", {"agent": "ghost", "kind": "acp", "task": "x"})
    ctx = JobContext(repo, await repo.get(job_id), worker_id="t", lease_ttl=timedelta(seconds=60))
    with pytest.raises(RuntimeError, match="unknown ACP agent"):
        await handler.agent_task(ctx)
