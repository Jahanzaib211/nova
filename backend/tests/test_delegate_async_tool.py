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


def test_capability_delegate_resolves_registry_ids_to_a_kind():
    """Live 2026-09-22: Claude Code called `agents__delegate` with
    `acp:claude_code` (the id agents__registry gave it) and the job died with
    `unknown subagent 'claude_code'` — the capability enqueued no `kind`."""
    from deerflow.capabilities.modules.agents import resolve_delegate_target

    config = AppConfig.model_validate({"sandbox": SANDBOX, "acp_agents": {"claude_code": {"command": "npx", "description": "x"}}})
    assert resolve_delegate_target("acp:claude_code", config) == ("claude_code", "acp")
    assert resolve_delegate_target("claude_code", config) == ("claude_code", "acp")
    assert resolve_delegate_target("subagent:bash", config) == ("bash", "subagent")
    assert resolve_delegate_target("general-purpose", config) == ("general-purpose", "subagent")
    with pytest.raises(ValueError, match="unknown agent 'ghost'"):
        resolve_delegate_target("ghost", config)


async def test_capability_delegate_enqueues_kind_and_model(repo, monkeypatch):
    from deerflow.capabilities.modules import agents as mod
    from deerflow.capabilities.types import OpContext

    config = AppConfig.model_validate({"sandbox": SANDBOX, "acp_agents": {"claude_code": {"command": "npx", "description": "x"}}})
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: config)
    monkeypatch.setattr(mod, "jobs_repo", lambda: repo)
    ctx = OpContext(user_id="u1", is_admin=False, thread_id="t1", surface="mcp")

    out = await mod._delegate(ctx, mod.DelegateIn(agent="acp:claude_code", task="probe"))
    assert out.job["payload"] == {"agent": "claude_code", "kind": "acp", "task": "probe"}

    out = await mod._delegate(ctx, mod.DelegateIn(agent="bash", task="uname -a", model="claude-sonnet-4-6"))
    assert out.job["payload"] == {"agent": "bash", "kind": "subagent", "task": "uname -a", "model": "claude-sonnet-4-6"}


async def test_agent_task_job_passes_the_requested_model_to_the_subagent(repo, monkeypatch):
    from app.jobs.handlers import agents as handler

    captured: dict = {}

    class FakeExecutor:
        def __init__(self, **kw):
            captured.update(kw)

        def execute_async(self, task, task_id=None):
            return task_id

    class Result:
        status = handler_status = None

    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", FakeExecutor)
    from deerflow.subagents.executor import SubagentStatus

    done = SimpleNamespace(status=SubagentStatus.COMPLETED, result="ok", error=None, ai_messages=[])
    monkeypatch.setattr("deerflow.subagents.executor.get_background_task_result", lambda task_id: done)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kw: [])

    job_id = await JobQueue(repo).enqueue("agents.task", {"agent": "bash", "kind": "subagent", "task": "id", "model": "claude-sonnet-4-6"})
    ctx = JobContext(repo, await repo.get(job_id), worker_id="t", lease_ttl=timedelta(seconds=60))
    out = await handler.agent_task(ctx)
    assert out["result"] == "ok"
    assert captured["parent_model"] == "claude-sonnet-4-6"


async def test_capability_delegate_from_a_runtime_uses_the_configured_delegate_model(repo, monkeypatch):
    from deerflow.capabilities.modules import agents as mod
    from deerflow.capabilities.types import OpContext

    config = AppConfig.model_validate({"sandbox": SANDBOX, "runtimes": {"enabled": True, "delegate_model": "claude-sonnet-4-6"}})
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: config)
    monkeypatch.setattr(mod, "jobs_repo", lambda: repo)

    # Over MCP (an ACP runtime's turn) the pinned model applies…
    out = await mod._delegate(OpContext(user_id="u1", is_admin=False, thread_id="t1", surface="mcp"), mod.DelegateIn(agent="bash", task="id"))
    assert out.job["payload"]["model"] == "claude-sonnet-4-6"
    # …an explicit choice still wins…
    out = await mod._delegate(OpContext(user_id="u1", is_admin=False, thread_id="t1", surface="mcp"), mod.DelegateIn(agent="bash", task="id", model="minimax-m3"))
    assert out.job["payload"]["model"] == "minimax-m3"
    # …and a native/API caller keeps inheriting the deployment default.
    out = await mod._delegate(OpContext(user_id="u1", is_admin=False, thread_id="t1", surface="api"), mod.DelegateIn(agent="bash", task="id"))
    assert "model" not in out.job["payload"]
