"""Hardening of the delegated-ACP path and the per-turn harness token.

* A delegated agent's token reaches the thread's sandbox and nothing else
  (it used to be minted with ``["*"]`` -- every platform op, as the user).
* The token revoke survives a second cancellation and logs failures loudly.
* A delegated turn's tokens reach the run's journal, in the subagent bucket.
* The chat runtime's usage dedup key never falls back to ``id()``.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from nova.config.acp_config import ACPAgentConfig

pytestmark = pytest.mark.anyio


# --- token scopes --------------------------------------------------------


class _RecordingRepo:
    def __init__(self, *_a, **_k) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, *, owner_user_id: str, name: str, scopes: list[str]) -> dict[str, Any]:
        self.created.append({"owner_user_id": owner_user_id, "name": name, "scopes": scopes})
        return {"id": "tok", "token": "nhk_x"}


@pytest.fixture
def mint_env(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingRepo]:
    from nova.config import app_config as app_config_module
    from nova.persistence import engine as engine_module
    from nova.persistence.harness_token import sql as token_sql

    repos: list[_RecordingRepo] = []

    def make_repo(sf):
        repo = _RecordingRepo()
        repos.append(repo)
        return repo

    monkeypatch.setattr(engine_module, "get_session_factory", lambda: object())
    monkeypatch.setattr(token_sql, "HarnessTokenRepository", make_repo)
    monkeypatch.setattr(app_config_module, "get_app_config", lambda: SimpleNamespace(runtimes=SimpleNamespace(nova_mcp_url="http://gw/api/mcp/nova")))
    return repos


async def test_delegate_token_is_scoped_to_the_sandbox_module(mint_env) -> None:
    from nova.runtimes.session import DELEGATE_SCOPES, default_nova_mcp
    from nova.runtimes.transcript import DELEGATE_ROLE

    server, _ = default_nova_mcp("u1", "thread-1", role=DELEGATE_ROLE)
    await server["__mint__"]()
    assert mint_env[0].created == [{"owner_user_id": "u1", "name": "runtime:thread-1", "scopes": list(DELEGATE_SCOPES)}]
    assert "*" not in DELEGATE_SCOPES


async def test_chat_runtime_token_keeps_every_module(mint_env) -> None:
    from nova.runtimes.session import default_nova_mcp

    server, _ = default_nova_mcp("u1", "thread-1")
    await server["__mint__"]()
    assert mint_env[0].created[0]["scopes"] == ["*"]


async def test_delegate_without_a_thread_gets_no_token(mint_env) -> None:
    from nova.runtimes.session import default_nova_mcp
    from nova.runtimes.transcript import DELEGATE_ROLE

    server, cleanup = default_nova_mcp("u1", None, role=DELEGATE_ROLE)
    assert server is None
    cleanup()
    assert mint_env == []


async def test_the_delegate_tool_mints_with_the_delegate_role(mint_env) -> None:
    from nova.runtimes.session import DELEGATE_SCOPES
    from nova.tools.builtins.invoke_acp_agent_tool import _nova_mcp

    server, _ = _nova_mcp("u1", "thread-9")
    await server["__mint__"]()
    assert mint_env[0].created[0]["scopes"] == list(DELEGATE_SCOPES)
    assert _nova_mcp("u1", None)[0] is None


def test_delegate_scopes_admit_every_sandbox_op_and_nothing_else() -> None:
    """Check the scopes against the real capability registry.

    Nova's MCP server admits an op when ``"*" in scopes or op.module_id in
    scopes`` (``Principal.allows``), for both ``tools/list`` and
    ``tools/call``; that path is exercised end to end by
    ``test_acp_runtime_drives_sandbox_e2e``.
    """
    from nova.capabilities import CapabilityRegistry
    from nova.capabilities.modules import register_builtin_modules
    from nova.runtimes.session import DELEGATE_SCOPES

    registry = CapabilityRegistry()
    register_builtin_modules(registry)
    ops = registry.operations()
    admitted = {op.name for op in ops if "*" in DELEGATE_SCOPES or op.module_id in DELEGATE_SCOPES}
    sandbox_ops = {op.name for op in ops if op.name.startswith("sandbox.")}

    assert {"sandbox.bash", "sandbox.write_file", "sandbox.expose_port"} <= sandbox_ops
    assert admitted == sandbox_ops
    assert {op.module_id for op in ops} - set(DELEGATE_SCOPES), "other modules exist and must stay out of reach"


# --- revoke under cancellation -------------------------------------------


async def test_revoke_survives_a_second_cancellation() -> None:
    """The session is cancelled, and then the revoke in its ``finally`` is
    cancelled again: the token must still end up revoked."""
    from nova.runtimes.session import open_acp_session

    revoke_started = asyncio.Event()
    release = asyncio.Event()
    revoked: list[str] = []
    entered = asyncio.Event()

    class SlowRepo:
        async def revoke(self, token_id, *, owner_user_id):
            revoke_started.set()
            await release.wait()
            revoked.append(token_id)

    def factory(user_id, thread_id):
        async def _mint():
            return {"id": "tok-1", "token": "nhk_x"}

        return {"__mint__": _mint, "url": "http://gw", "repo": SlowRepo()}, lambda: None

    async def turn() -> None:
        async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=factory, client_servers=lambda: []):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.ensure_future(turn())
    await entered.wait()
    task.cancel()  # first cancellation: the run is stopped
    await revoke_started.wait()
    task.cancel()  # second cancellation lands on the revoke itself
    with pytest.raises(asyncio.CancelledError):
        await task
    assert revoked == []  # still in flight -- not abandoned
    release.set()
    for _ in range(20):
        if revoked:
            break
        await asyncio.sleep(0)
    assert revoked == ["tok-1"]


async def test_a_failed_revoke_is_logged_at_warning(caplog) -> None:
    from nova.runtimes.session import open_acp_session

    class BrokenRepo:
        async def revoke(self, token_id, *, owner_user_id):
            raise RuntimeError("db down")

    def factory(user_id, thread_id):
        async def _mint():
            return {"id": "tok-1", "token": "nhk_x"}

        return {"__mint__": _mint, "url": "http://gw", "repo": BrokenRepo()}, lambda: None

    caplog.set_level(logging.WARNING, logger="nova.runtimes.session")
    async with open_acp_session(agent="claude_code", user_id="u1", thread_id="t1", nova_mcp=factory, client_servers=lambda: []):
        pass
    records = [r for r in caplog.records if "tok-1" in r.getMessage()]
    assert records and records[0].levelno == logging.WARNING


# --- delegated usage -----------------------------------------------------


class _RecordingStore:
    async def put_batch(self, events):
        return None

    async def put(self, *a, **k):
        return None


def _usage(total: int = 300):
    from nova.runtimes.acp_transport import ACPTurnUsage

    return ACPTurnUsage.from_acp(SimpleNamespace(input_tokens=total - 50, output_tokens=50, total_tokens=total))


async def test_delegated_turn_usage_lands_in_the_subagent_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from nova.runtime.journal import RunJournal
    from nova.runtimes import acp_transport
    from nova.runtimes.acp_transport import ACPText
    from nova.tools.builtins import invoke_acp_agent_tool as tool_module

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    from nova.config import paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    monkeypatch.setattr(tool_module, "_nova_mcp", lambda u, t: (None, lambda: None))
    monkeypatch.setattr(tool_module, "_build_acp_mcp_servers", lambda: [])

    async def adapter(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
        return ACPText("built it", usage=_usage(300), session_id="sess-d")

    monkeypatch.setattr(acp_transport, "run_acp_prompt", adapter)
    journal = RunJournal(run_id="run-1", thread_id="thread-d", event_store=_RecordingStore())  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "thread-d", "__pregel_runtime": SimpleNamespace(context={"__run_journal": journal})}}

    tool = tool_module.build_invoke_acp_agent_tool({"claude_code": ACPAgentConfig(command="npx", description="Claude Code", model="claude-sonnet-4")})
    result = await tool.coroutine(agent="claude_code", prompt="task", config=config)
    # Calling again with the same session must not double-count.
    await tool.coroutine(agent="claude_code", prompt="task", config=config)

    assert result == "built it"
    data = journal.get_completion_data()
    assert data["total_tokens"] == 300
    assert data["subagent_tokens"] == 300
    assert data["lead_agent_tokens"] == 0


async def test_delegated_usage_is_found_through_the_callbacks_too() -> None:
    from nova.runtimes.acp_transport import ACPText
    from nova.tools.builtins.invoke_acp_agent_tool import _record_delegated_usage

    class Journal:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def record_external_llm_usage_records(self, records):
            self.records.extend(records)

    journal = Journal()
    _record_delegated_usage({"callbacks": [object(), journal]}, "codex", None, ACPText("x", usage=_usage(10)))
    assert len(journal.records) == 1
    record = journal.records[0]
    assert record["caller"] == "subagent:acp:codex"
    assert record["model_name"] == "acp:codex"
    # No journal / no usage / broken journal: never raises.
    _record_delegated_usage({}, "codex", None, ACPText("x", usage=_usage(10)))
    _record_delegated_usage({"callbacks": [journal]}, "codex", None, "plain")
    assert len(journal.records) == 1


# --- middleware dedup key ------------------------------------------------


async def test_chat_runtime_usage_without_session_id_never_collides() -> None:
    from nova.runtimes.acp_transport import ACPText
    from nova.runtimes.middleware import RuntimeDispatchMiddleware

    class Journal:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def record_external_llm_usage_records(self, records):
            self.records.extend(records)

    journal = Journal()
    mw = RuntimeDispatchMiddleware()
    for _ in range(3):
        text = ACPText("x", usage=_usage(10))
        mw._record_turn_usage({"__run_journal": journal}, "claude_code", None, text)
        del text  # free it so CPython may reuse the id -- the old key's failure mode
    ids = [r["source_run_id"] for r in journal.records]
    assert len(set(ids)) == 3
