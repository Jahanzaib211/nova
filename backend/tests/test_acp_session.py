"""One ACP session builder for both ways Nova runs an external agent.

A chat on the ``claude_code`` runtime (``RuntimeDispatchMiddleware``) and a
subtask the lead agent hands to Claude Code (``invoke_acp_agent``) used to
build their sessions separately, and the second one got none of the first's
fixes: no Nova MCP server, no briefing, no Activity mirroring. Seen live on
2026-11-01 — a delegated Claude Code had no way into the user's sandbox, its
own ``Write`` was denied by policy, ACP reported the denial as "cancelled",
and the model told the user to "approve the permission prompt" that no human
was ever shown. ``open_acp_session`` is now the only place either path gets
its servers, briefing and mirror from.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from deerflow.config.acp_config import ACPAgentConfig


def _nova_factory(log: list[str], *, fail_mint: bool = False):
    """A factory shaped like ``default_nova_mcp``: a lazy mint plus a repo to revoke with."""

    class Repo:
        async def revoke(self, token_id, *, owner_user_id):
            log.append(f"revoke:{token_id}:{owner_user_id}")

    def factory(user_id, thread_id):
        async def _mint():
            if fail_mint:
                raise RuntimeError("token store unavailable")
            log.append(f"mint:{user_id}:{thread_id}")
            return {"id": "tok-1", "token": "nhk_secret"}

        return {"__mint__": _mint, "url": "http://gw/api/mcp/nova", "repo": Repo()}, lambda: log.append("cleanup")

    return factory


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    monkeypatch.setattr(paths_module, "_paths_singleton", None, raising=False)
    return tmp_path


def _log_lines(home: Path) -> list[dict[str, Any]]:
    logs = list(home.rglob("sandbox.log"))
    assert len(logs) == 1, logs
    return [json.loads(line) for line in logs[0].read_text().splitlines() if line.strip()]


# --- the builder ---------------------------------------------------------


@pytest.mark.anyio
async def test_session_mounts_nova_first_then_client_servers_and_revokes_after():
    from deerflow.runtimes.session import open_acp_session

    log: list[str] = []
    client = [{"name": "github", "type": "http", "url": "http://mcp/github", "headers": []}]
    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=_nova_factory(log), client_servers=lambda: client) as session:
        assert session.nova_mounted is True
        assert [m["name"] for m in session.mcp_servers] == ["nova", "github"]
        assert session.mcp_servers[0]["headers"] == [{"name": "Authorization", "value": "Bearer nhk_secret"}]
        assert log == ["mint:u1:t1"]
    assert log == ["mint:u1:t1", "revoke:tok-1:u1", "cleanup"]


@pytest.mark.anyio
async def test_session_revokes_the_token_even_when_the_turn_raises():
    from deerflow.runtimes.session import open_acp_session

    log: list[str] = []
    with pytest.raises(ValueError, match="boom"):
        async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=_nova_factory(log), client_servers=lambda: []):
            raise ValueError("boom")
    assert "revoke:tok-1:u1" in log


@pytest.mark.anyio
async def test_invalid_client_servers_are_logged_and_skipped(caplog):
    from deerflow.runtimes.session import open_acp_session

    def broken():
        raise ValueError("missing command")

    caplog.set_level(logging.WARNING)
    async with open_acp_session(agent="codex", user_id="u1", thread_id="t1", nova_mcp=lambda u, t: (None, lambda: None), client_servers=broken) as session:
        assert session.mcp_servers == []
    assert "continuing without MCP servers" in caplog.text and "missing command" in caplog.text


@pytest.mark.anyio
async def test_a_failed_mount_raises_unless_the_caller_degrades():
    from deerflow.runtimes.session import open_acp_session

    with pytest.raises(RuntimeError, match="token store unavailable"):
        async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=_nova_factory([], fail_mint=True), client_servers=lambda: []):
            pass

    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=_nova_factory([], fail_mint=True), client_servers=lambda: [], degrade_on_mount_error=True) as session:
        assert session.nova_mounted is False
        assert session.mcp_servers == []


# --- the briefing --------------------------------------------------------


def test_permission_contract_is_sent_with_and_without_nova():
    from deerflow.runtimes.transcript import permission_contract

    with_nova = permission_contract(nova_mounted=True)
    without = permission_contract(nova_mounted=False)
    for text in (with_nova, without):
        assert "no human" in text.lower()
        assert "denied by policy" in text
        assert "never ask the user to approve" in text.lower()
    assert "`nova`" in with_nova
    assert "report" in without.lower()


@pytest.mark.anyio
async def test_briefing_names_the_sandbox_and_the_permission_contract():
    from deerflow.runtimes.session import open_acp_session

    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=_nova_factory([]), client_servers=lambda: []) as session:
        briefed = session.brief("build the site")
    assert briefed.startswith("You are running as the runtime for a Nova chat")
    assert "`sandbox__write_file`" in briefed
    assert "denied by policy" in briefed
    assert briefed.endswith("build the site")


@pytest.mark.anyio
async def test_briefing_without_nova_still_carries_the_permission_contract():
    from deerflow.runtimes.session import open_acp_session

    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=lambda u, t: (None, lambda: None), client_servers=lambda: []) as session:
        briefed = session.brief("build the site")
    assert "sandbox__" not in briefed
    assert "denied by policy" in briefed
    assert briefed.endswith("build the site")


# --- the mirror ----------------------------------------------------------


@pytest.mark.anyio
async def test_session_mirror_writes_actions_under_the_agents_name(home: Path):
    from deerflow.runtimes.session import open_acp_session

    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="thread-m", nova_mcp=lambda u, t: (None, lambda: None), client_servers=lambda: []) as session:
        session.mirror("thinking: hmm")
        session.mirror("edit: Write(/tmp/site/index.html)")
        session.mirror("other: mcp__nova__sandbox__write_file")
        session.mirror("permission denied: edit — Write")
    lines = _log_lines(home)
    assert [line["type"] for line in lines] == ["acp_edit", "acp_permission_denied"]
    assert lines[0]["summary"] == "claude_code: Write(/tmp/site/index.html)"


# --- delegation uses it --------------------------------------------------


@pytest.mark.anyio
async def test_delegated_agent_gets_nova_the_briefing_and_the_mirror(home: Path, monkeypatch: pytest.MonkeyPatch):
    """The car-dealership regression: a delegated Claude Code must get the
    same hands as a chat on the claude_code runtime."""
    from deerflow.runtimes import acp_transport
    from deerflow.tools.builtins import invoke_acp_agent_tool as tool_module

    log: list[str] = []
    monkeypatch.setattr(tool_module, "_nova_mcp", _nova_factory(log))
    monkeypatch.setattr(tool_module, "_build_acp_mcp_servers", lambda: [])
    seen: dict[str, Any] = {}

    async def adapter(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen.update(prompt=prompt, mcp_servers=mcp_servers, permission=permission)
        on_status("s-1", "edit: Write(/app/acp-workspace/index.html)")
        on_status("s-1", "permission denied: edit — Write")
        on_text("s-1", "done")
        return "done"

    monkeypatch.setattr(acp_transport, "run_acp_prompt", adapter)
    events: list[dict] = []
    monkeypatch.setattr(tool_module, "_stream_writer", lambda: events.append)

    tool = tool_module.build_invoke_acp_agent_tool({"claude_code": ACPAgentConfig(command="npx", description="Claude Code")})
    result = await tool.coroutine(agent="claude_code", prompt="build a three.js site", config={"configurable": {"thread_id": "thread-d"}})

    assert result == "done"
    assert seen["mcp_servers"][0]["name"] == "nova"
    assert seen["prompt"].startswith("You are running as a subagent Nova delegated a task to")
    assert "`sandbox__write_file`" in seen["prompt"] and "denied by policy" in seen["prompt"]
    assert seen["prompt"].endswith("build a three.js site")
    # Nova's own write/execute ops are judged by operation kind in "standard",
    # so the sandbox tools work while Claude Code's private Write stays denied.
    assert seen["permission"].mode == "standard"
    from deerflow.runtime.user_context import get_effective_user_id

    owner = get_effective_user_id()
    assert log[0] == f"mint:{owner}:thread-d" and f"revoke:tok-1:{owner}" in log

    assert [line["type"] for line in _log_lines(home)] == ["acp_edit", "acp_permission_denied"]
    assert all(e["type"] == "acp_update" and e["agent"] == "claude_code" for e in events)
    assert [e["kind"] for e in events] == ["status", "status", "text"]


@pytest.mark.anyio
async def test_delegation_degrades_to_its_own_workspace_when_nova_cannot_mount(home: Path, monkeypatch: pytest.MonkeyPatch):
    from deerflow.runtimes import acp_transport
    from deerflow.tools.builtins import invoke_acp_agent_tool as tool_module

    monkeypatch.setattr(tool_module, "_nova_mcp", _nova_factory([], fail_mint=True))
    monkeypatch.setattr(tool_module, "_build_acp_mcp_servers", lambda: [])
    seen: dict[str, Any] = {}

    async def adapter(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen.update(prompt=prompt, mcp_servers=mcp_servers)
        return "worked in my own workspace"

    monkeypatch.setattr(acp_transport, "run_acp_prompt", adapter)
    tool = tool_module.build_invoke_acp_agent_tool({"claude_code": ACPAgentConfig(command="npx", description="Claude Code")})
    result = await tool.coroutine(agent="claude_code", prompt="task", config={"configurable": {"thread_id": "thread-x"}})

    assert result == "worked in my own workspace"
    assert seen["mcp_servers"] == []
    assert "denied by policy" in seen["prompt"] and "sandbox__" not in seen["prompt"]


def test_tool_description_is_accurate_with_and_without_the_sandbox():
    from deerflow.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

    description = build_invoke_acp_agent_tool({"claude_code": ACPAgentConfig(command="npx", description="Claude Code")}).description
    assert "/mnt/user-data" in description
    assert "/mnt/acp-workspace/" in description
    assert "Do NOT include /mnt/user-data paths" not in description


# --- the deadline --------------------------------------------------------


@pytest.mark.anyio
async def test_session_passes_its_turn_budget_to_the_adapter():
    from deerflow.runtimes.session import open_acp_session

    seen: dict[str, Any] = {}

    async def adapter(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen.update(timeout=timeout, prompt=prompt, mcp_servers=mcp_servers)
        return "ok"

    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=lambda u, t: (None, lambda: None), client_servers=lambda: [], turn_timeout=42.0) as session:
        assert session.turn_timeout == 42.0
        text = await session.run(adapter, ACPAgentConfig(command="npx", description="cc"), "task", cwd="/tmp", permission=None, on_text=lambda s, d: None, on_status=lambda s, d: None)
    assert text == "ok"
    assert seen["timeout"] == 42.0
    assert seen["prompt"].endswith("task") and "denied by policy" in seen["prompt"]


def test_turn_budget_defaults_to_the_configured_runtime_budget(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from deerflow.config import app_config as app_config_module
    from deerflow.runtimes import session as session_module

    monkeypatch.setattr(app_config_module, "get_app_config", lambda: SimpleNamespace(runtimes=SimpleNamespace(turn_timeout_seconds=900.0)))
    assert session_module.default_turn_timeout() == 900.0

    def broken():
        raise ValueError("no config")

    monkeypatch.setattr(app_config_module, "get_app_config", broken)
    assert session_module.default_turn_timeout() == session_module.DEFAULT_TURN_TIMEOUT == 1800.0


@pytest.mark.anyio
async def test_a_hung_delegated_agent_ends_with_a_clear_timeout_error(home: Path, monkeypatch: pytest.MonkeyPatch):
    """invoke_acp_agent used to call run_acp_prompt with no timeout at all, so
    a wedged adapter held the lead agent's tool call -- and the run -- open
    forever. The session's deadline bounds it even when the runner ignores
    its own ``timeout`` argument."""
    import asyncio

    from deerflow.runtimes import acp_transport
    from deerflow.runtimes import session as session_module
    from deerflow.tools.builtins import invoke_acp_agent_tool as tool_module

    monkeypatch.setattr(session_module, "default_turn_timeout", lambda: 0.2)
    monkeypatch.setattr(session_module, "TIMEOUT_GRACE_S", 0.1)
    monkeypatch.setattr(tool_module, "_nova_mcp", lambda u, t: (None, lambda: None))
    monkeypatch.setattr(tool_module, "_build_acp_mcp_servers", lambda: [])
    seen: dict[str, Any] = {}

    async def hung(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen["timeout"] = timeout
        await asyncio.Event().wait()  # never answers, and ignores `timeout`

    monkeypatch.setattr(acp_transport, "run_acp_prompt", hung)
    tool = tool_module.build_invoke_acp_agent_tool({"claude_code": ACPAgentConfig(command="npx", description="Claude Code")})
    result = await asyncio.wait_for(tool.coroutine(agent="claude_code", prompt="task", config={"configurable": {"thread_id": "thread-h"}}), timeout=10)

    assert seen["timeout"] == 0.2
    assert result.startswith("Error invoking ACP agent 'claude_code'")
    assert "did not finish within" in result and "runtimes.turn_timeout_seconds" in result


@pytest.mark.anyio
async def test_the_chat_path_turn_timeout_still_falls_back_to_native():
    from langchain_core.messages import AIMessage, HumanMessage

    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware
    from deerflow.runtimes.registry import RuntimeRegistry
    from deerflow.runtimes.session import ACPTurnTimeout

    seen: dict[str, Any] = {}

    async def slow(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen["timeout"] = timeout
        raise TimeoutError()

    agents = {"claude_code": ACPAgentConfig(command="npx", description="cc")}
    mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=agents, default="native"), run_prompt=slow, stream_writer=lambda: None, nova_mcp=lambda u, t: (None, lambda: None), turn_timeout=1234.0)

    class Req:
        messages = [HumanMessage("hi")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "claude_code"}

    async def handler(req):
        return AIMessage("native answer")

    result = await mw.awrap_model_call(Req(), handler)
    assert seen["timeout"] == 1234.0
    assert result.content == "native answer"
    assert result.response_metadata["runtime_fallback_reason"].startswith("TimeoutError")
    assert issubclass(ACPTurnTimeout, TimeoutError)
