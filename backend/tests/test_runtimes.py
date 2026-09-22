"""Runtime registry: Claude Code / OpenClaw as first-class runtimes for whole
chats, selected per model or per chat, with permission-mode presets.

Mirrors OpenClaw's `agentRuntime.id`: a thread override wins over the
model's `runtime:`, which wins over the default (`native`, the LangGraph
lead agent). The dispatch middleware swaps the model call for an ACP
session when a non-native runtime is selected — threads, checkpoints,
titles, memory and the `acp_update` transcript stream are untouched.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes import (
    NATIVE,
    PERMISSION_MODES,
    RuntimeRegistry,
    RuntimeSelection,
    policy_for_mode,
    transcript_for_prompt,
)

pytestmark = pytest.mark.no_auto_user


def _agents() -> dict[str, ACPAgentConfig]:
    return {
        "claude_code": ACPAgentConfig(command="npx", args=["-y", "@zed-industries/claude-agent-acp@0.23.1"], description="Claude Code"),
        "openclaw": ACPAgentConfig(command="node", args=["/opt/openclaw/openclaw.mjs", "acp"], description="OpenClaw"),
    }


# ---------------------------------------------------------------- policy presets


def test_permission_modes_map_to_acp_policies():
    assert set(PERMISSION_MODES) == {"full", "standard", "plan"}
    full = policy_for_mode("full")
    assert full.auto_approve is True and "delete" in full.policy.deny_kinds
    standard = policy_for_mode("standard")
    assert standard.auto_approve is False
    assert {"read", "search", "fetch", "think"} <= set(standard.policy.allow_kinds)
    assert "execute" not in standard.policy.allow_kinds
    plan = policy_for_mode("plan")
    assert plan.auto_approve is False
    assert {"edit", "delete", "move", "execute"} <= set(plan.policy.deny_kinds)
    with pytest.raises(ValueError):
        policy_for_mode("yolo")


# ---------------------------------------------------------------- selection


def test_selection_precedence_thread_over_model_over_default():
    reg = RuntimeRegistry(acp_agents=_agents(), default="native")
    assert reg.ids() == ["claude_code", "native", "openclaw"]
    # default
    sel = reg.select(context={}, model_runtime=None)
    assert sel == RuntimeSelection(runtime="native", account="auto", permission_mode="standard", source="default")
    # model-level
    sel = reg.select(context={}, model_runtime="claude_code")
    assert sel.runtime == "claude_code" and sel.source == "model"
    # chat-level wins
    sel = reg.select(context={"runtime": "openclaw", "permission_mode": "plan", "runtime_account": "gateway-token"}, model_runtime="claude_code")
    assert sel.runtime == "openclaw" and sel.permission_mode == "plan" and sel.account == "gateway-token" and sel.source == "thread"
    # unknown values degrade to defaults, never raise mid-run
    sel = reg.select(context={"runtime": "nope", "permission_mode": "yolo"}, model_runtime=None)
    assert sel.runtime == "native" and sel.permission_mode == "standard"
    # explicit reset ("native") on a model that prefers claude_code
    sel = reg.select(context={"runtime": "native"}, model_runtime="claude_code")
    assert sel.runtime == "native" and sel.source == "thread"


def test_accounts_report_availability_without_reading_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg = RuntimeRegistry(acp_agents=_agents(), default="native", claude_login_dir=tmp_path / "claude", openclaw_token_file=tmp_path / "tok")
    accounts = {a.id: a for a in reg.accounts_for("claude_code")}
    assert accounts["claude-login"].available is False
    assert accounts["anthropic-api-key"].available is False
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / ".credentials.json").write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    accounts = {a.id: a for a in reg.accounts_for("claude_code")}
    assert accounts["claude-login"].available is True and accounts["anthropic-api-key"].available is True
    assert reg.accounts_for("openclaw")[0].available is False
    (tmp_path / "tok").write_text("t")
    assert reg.accounts_for("openclaw")[0].available is True
    assert reg.accounts_for("native")[0].id == "configured-models"
    assert reg.resolve_account("claude_code", "auto").id == "claude-login"


def test_describe_is_json_and_lists_modes():
    reg = RuntimeRegistry(acp_agents=_agents(), default="native")
    d = reg.describe()
    assert [r["id"] for r in d["runtimes"]] == ["claude_code", "native", "openclaw"]
    assert d["default"] == "native" and d["permission_modes"] == list(PERMISSION_MODES)
    native = next(r for r in d["runtimes"] if r["id"] == "native")
    assert native["kind"] == "native" and native["accounts"][0]["id"] == "configured-models"


# ---------------------------------------------------------------- transcript


def test_transcript_for_prompt_keeps_last_turns_and_marks_the_request():
    msgs = [HumanMessage("first"), AIMessage("reply one"), HumanMessage("second"), AIMessage("reply two"), HumanMessage("do the thing")]
    text = transcript_for_prompt(msgs, max_prior_turns=1)
    assert text.endswith("do the thing")
    assert "reply two" in text and "reply one" not in text
    assert "Earlier in this conversation" in text


# ---------------------------------------------------------------- middleware


@pytest.mark.anyio
async def test_dispatch_middleware_routes_to_acp_and_streams(monkeypatch):
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    calls: dict = {}

    async def fake_run(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        calls["prompt"] = prompt
        calls["mcp"] = mcp_servers
        calls["permission"] = permission
        on_status("s-1", "tool: ls")
        on_text("s-1", "hello ")
        on_text("s-1", "world")
        return "hello world"

    events: list[dict] = []
    mw = RuntimeDispatchMiddleware(
        registry=RuntimeRegistry(acp_agents=_agents(), default="native"),
        run_prompt=fake_run,
        stream_writer=lambda: events.append,
        nova_mcp=lambda user_id, thread_id: ({"name": "nova", "type": "http", "url": "http://gw/api/mcp/nova", "headers": [{"name": "Authorization", "value": "Bearer nhk_x"}]}, lambda: None),
    )

    class Req:
        messages = [HumanMessage("ship it")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "claude_code", "permission_mode": "plan", "thread_id": "t1", "user_id": "u1"}

    async def handler(req):
        raise AssertionError("native model must not be called")

    result = await mw.awrap_model_call(Req(), handler)
    assert isinstance(result, AIMessage) and result.content == "hello world"
    assert result.response_metadata["runtime"] == "claude_code"
    assert calls["prompt"].endswith("ship it")
    assert calls["mcp"][0]["name"] == "nova"
    assert calls["permission"].auto_approve is False and "execute" in calls["permission"].policy.deny_kinds
    kinds = [e["kind"] for e in events]
    assert events[0]["type"] == "acp_update" and events[0]["agent"] == "claude_code"
    assert events[-1]["session_id"] == "s-1"
    assert "status" in kinds and "text" in kinds


@pytest.mark.anyio
async def test_dispatch_middleware_passes_through_for_native():
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=_agents(), default="native"))

    class Req:
        messages = [HumanMessage("hi")]
        state = {"messages": messages}

        class runtime:
            context = {}

    async def handler(req):
        return AIMessage("native answer")

    assert (await mw.awrap_model_call(Req(), handler)).content == "native answer"


@pytest.mark.anyio
async def test_dispatch_middleware_falls_back_to_native_when_the_runtime_fails():
    """Live 2026-09-21: Claude Code answered `You've hit your limit · resets
    3am (UTC)` five minutes into the turn and the user got that sentence as
    the reply. The same turn must instead run on the native agent — full
    tool suite — and say why in the stream and on the message."""
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    async def failing(*a, **k):
        raise RuntimeError("Internal error: You've hit your limit · resets 3am (UTC)")

    events: list[dict] = []
    mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=_agents(), default="native"), run_prompt=failing, stream_writer=lambda: events.append, nova_mcp=lambda u, t: (None, lambda: None))

    class Req:
        messages = [HumanMessage("hi")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "claude_code", "thread_id": "t1"}

    seen: list = []

    async def handler(req):
        seen.append(req)
        return AIMessage("native answer")

    result = await mw.awrap_model_call(Req(), handler)
    assert seen and isinstance(seen[0], Req)
    assert result.content == "native answer"
    assert result.response_metadata["runtime"] == "native"
    assert result.response_metadata["runtime_fallback_from"] == "claude_code"
    assert "hit your limit" in result.response_metadata["runtime_fallback_reason"]
    statuses = [e["delta"] for e in events if e["kind"] == "status"]
    assert any("unavailable" in s and "native" in s and "hit your limit" in s for s in statuses)


@pytest.mark.anyio
async def test_dispatch_middleware_fallback_covers_the_turn_timeout():
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    async def slow(*a, **k):
        raise TimeoutError()

    mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=_agents(), default="native"), run_prompt=slow, stream_writer=lambda: None, nova_mcp=lambda u, t: (None, lambda: None))

    class Req:
        messages = [HumanMessage("hi")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "openclaw"}

    async def handler(req):
        return AIMessage("native answer")

    result = await mw.awrap_model_call(Req(), handler)
    assert result.content == "native answer"
    assert result.response_metadata["runtime_fallback_reason"].startswith("TimeoutError")


@pytest.mark.anyio
async def test_dispatch_middleware_reports_failures_as_a_message_when_fallback_is_off():
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    async def failing(*a, **k):
        raise RuntimeError("adapter exited 1")

    mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=_agents(), default="native"), run_prompt=failing, stream_writer=lambda: None, nova_mcp=lambda u, t: (None, lambda: None), fallback_to_native=False)

    class Req:
        messages = [HumanMessage("hi")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "openclaw"}

    async def handler(req):
        raise AssertionError

    result = await mw.awrap_model_call(Req(), handler)
    assert "openclaw" in result.content and "adapter exited 1" in result.content
    assert result.response_metadata["runtime_error"] is True


# ---------------------------------------------------------------- Nova MCP tools under presets


def test_nova_mcp_tool_calls_are_judged_by_operation_kind_not_acp_kind():
    """Claude Code surfaces MCP tool calls with ACP kind `other`; Nova's own
    tools (`mcp__nova__<module>__<op>`) are judged by the operation's kind
    from the capability registry: reads everywhere, writes/executes in
    standard and full, never in plan."""
    from deerflow.runtimes.acp_transport import nova_tool_decision

    kinds = {"jobs.list": "read", "jobs.enqueue": "execute", "jobs.cancel": "write", "secrets.set": "secret"}
    lookup = lambda name: kinds.get(name)  # noqa: E731
    assert nova_tool_decision("mcp__nova__jobs__list", "plan", lookup) is True
    assert nova_tool_decision("mcp__nova__jobs__enqueue", "plan", lookup) is False
    assert nova_tool_decision("mcp__nova__jobs__enqueue", "standard", lookup) is True
    assert nova_tool_decision("mcp__nova__jobs__cancel", "full", lookup) is True
    assert nova_tool_decision("mcp__nova__secrets__set", "full", lookup) is False
    assert nova_tool_decision("mcp__nova__nope__x", "full", lookup) is None  # unknown op: fall back to the ACP policy
    assert nova_tool_decision("Bash", "full", lookup) is None  # not a Nova tool


# ---------------------------------------------------------------- the runtime's hands


def test_runtime_preamble_points_at_novas_sandbox_only_when_nova_is_mounted():
    from deerflow.runtimes.transcript import SANDBOX_TOOL_NAMES, runtime_preamble

    text = runtime_preamble(["nova", "other"])
    for name in SANDBOX_TOOL_NAMES:
        assert f"`{name}`" in text
    assert "/mnt/user-data/workspace" in text
    assert "built-in Bash is unavailable" in text
    assert runtime_preamble(["other"]) == ""
    assert runtime_preamble([]) == ""


@pytest.mark.anyio
async def test_dispatch_middleware_briefs_the_runtime_and_mirrors_its_actions(tmp_path, monkeypatch):
    """Live 2026-09-22: Claude Code said "Bash is blocked" and worked in its
    private scratch dir; the Agent's Computer showed "0 actions". The prompt
    must open with the sandbox briefing, and each of the runtime's own tool
    calls must land in the thread's sandbox.log as an `acp_*` observation
    (narration and Nova-tool echoes excluded)."""
    import json

    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    monkeypatch.setattr(paths_module, "_paths_singleton", None, raising=False)

    thread_id = "thread-mirror-1"
    seen: dict = {}

    async def fake_run(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        seen["prompt"] = prompt
        on_status("s-1", "runtime: claude_code (plan)")
        on_status("s-1", "thinking: let me look around")
        on_status("s-1", "read: Read(/app/backend/config.yaml)")
        on_status("s-1", "other: mcp__nova__sandbox__bash")  # logged by the sandbox tool itself
        on_status("s-1", "permission denied: execute — rm -rf /")
        on_text("s-1", "done")
        return "done"

    mw = RuntimeDispatchMiddleware(
        registry=RuntimeRegistry(acp_agents=_agents(), default="native"),
        run_prompt=fake_run,
        stream_writer=lambda: None,
        nova_mcp=lambda user_id, thread_id: ({"name": "nova", "type": "http", "url": "http://gw/api/mcp/nova", "headers": []}, lambda: None),
    )

    class Req:
        messages = [HumanMessage("drive the sandbox")]
        state = {"messages": messages}

        class runtime:
            context = {"runtime": "claude_code", "thread_id": thread_id, "user_id": "u1"}

    async def handler(req):
        raise AssertionError

    result = await mw.awrap_model_call(Req(), handler)
    assert result.content == "done"
    assert seen["prompt"].startswith("You are running as the runtime for a Nova chat")
    assert "`sandbox__bash`" in seen["prompt"]
    assert seen["prompt"].endswith("drive the sandbox")

    from deerflow.config.paths import get_paths

    log = get_paths().sandbox_log_file(thread_id, user_id="u1") if hasattr(get_paths(), "sandbox_log_file") else None
    if log is None:
        candidates = list(tmp_path.rglob("sandbox.log"))
        assert len(candidates) == 1, candidates
        log = candidates[0]
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert [line["type"] for line in lines] == ["acp_read", "acp_permission_denied"]
    assert lines[0]["summary"] == "claude_code: Read(/app/backend/config.yaml)"
    assert lines[1]["summary"] == "claude_code: execute — rm -rf /"
    assert str(thread_id) in str(log)
