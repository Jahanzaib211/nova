"""An ACP runtime (Claude Code, OpenClaw) driving the Agent's Computer end to end.

The chain under test is the one a chat on the ``claude_code`` runtime uses:

    RuntimeDispatchMiddleware
      -> mints ``runtime:<thread_id>`` harness token, mounts Nova's MCP server
      -> ACP adapter (here: a stand-in that speaks real MCP)
         -> ``sandbox__write_file`` / ``sandbox__bash`` / ``sandbox__read_file``
            over streamable-HTTP against the in-process gateway
         -> LocalSandboxProvider in the thread's own user-data directory
      -> AIMessage carrying the runtime's answer

Everything is real except the Claude binary: real MCP client and server,
real capability registry, real sandbox tools, real per-user thread layout.
Before this existed the path had unit coverage per layer and no proof the
layers agreed — and they did not: the MCP server never bound the token's
owner as the current user, so a harness-driven write landed under
``users/default/`` while the chat's thread lived under the real owner
(seen live 2026-09-22: "it wrote the file" and the Files tab stayed empty).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.no_auto_user

_CONFIG_YAML = """\
log_level: info
models:
  - name: fake-test-model
    display_name: Fake Test Model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_API_BASE
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  # Host bash is opt-in on the local provider; the test runs inside a tmp dir.
  allow_host_bash: true
title:
  enabled: false
memory:
  enabled: false
database:
  backend: sqlite
run_events:
  backend: memory
"""

USER = "a1e0804a-owner"
THREAD = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(cfg))
    ext = tmp_path / "extensions_config.json"
    ext.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(ext))

    from deerflow.config import app_config as app_config_module
    from deerflow.config import extensions_config as extensions_config_module
    from deerflow.config import paths as paths_module
    from deerflow.persistence import engine as engine_module
    from deerflow.sandbox import sandbox_provider as provider_module

    for module, attr, value in (
        (app_config_module, "_app_config", None),
        (app_config_module, "_app_config_path", None),
        (app_config_module, "_app_config_mtime", None),
        (app_config_module, "_app_config_is_custom", False),
        (extensions_config_module, "_extensions_config", None),
        (paths_module, "_paths_singleton", None),
        (paths_module, "_paths", None),
        (engine_module, "_engine", None),
        (engine_module, "_session_factory", None),
        (provider_module, "_default_sandbox_provider", None),
    ):
        monkeypatch.setattr(module, attr, value, raising=False)
    yield home
    import asyncio

    from deerflow.persistence.engine import close_engine

    asyncio.run(close_engine())


async def _gateway(tmp_path: Path):
    """Nova's MCP server on the real capability registry with the *real*
    feature flags. A hard-coded ``{"sandbox": True}`` here once hid that
    ``compute_flags()`` had no sandbox key at all, so production never listed
    ``sandbox__*`` to Claude Code while this test stayed green."""
    from app.gateway.mcp_server import NovaMcpServer
    from deerflow.capabilities import CapabilityRegistry
    from deerflow.capabilities.modules import register_builtin_modules
    from deerflow.capabilities.modules.features import compute_flags
    from deerflow.persistence.engine import get_session_factory, init_engine
    from deerflow.persistence.harness_token.sql import HarnessTokenRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}", sqlite_dir=str(tmp_path))
    tokens = HarnessTokenRepository(get_session_factory())
    registry = CapabilityRegistry()
    register_builtin_modules(registry)
    server = NovaMcpServer(registry, tokens, flags=compute_flags)
    app = FastAPI()
    server.mount(app)
    await server.start()
    return app, server, tokens


class _Mcp:
    """A real MCP ClientSession over the in-process ASGI app — what the ACP
    adapter does with the ``mcp_servers`` entry ``new_session`` receives."""

    def __init__(self, app: FastAPI, url: str, headers: list[dict[str, str]]):
        self.app = app
        self.url = url
        self.headers = {h["name"]: h["value"] for h in headers}

    async def __aenter__(self) -> ClientSession:
        self._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://nova", headers=self.headers, timeout=30)
        self._cm = streamable_http_client(self.url, http_client=self._http)
        read, write, _ = await self._cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, *exc):
        await self._session_cm.__aexit__(*exc)
        await self._cm.__aexit__(*exc)
        await self._http.aclose()


def _text(result: Any) -> dict[str, Any]:
    assert result.isError is False, result.content
    return result.structuredContent or json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_harness_token_drives_the_threads_own_sandbox_over_mcp(home: Path, tmp_path: Path):
    app, server, tokens = await _gateway(tmp_path)
    try:
        tok = (await tokens.create(owner_user_id=USER, name=f"runtime:{THREAD}", scopes=["*"]))["token"]
        async with _Mcp(app, "http://nova/api/mcp/nova", [{"name": "Authorization", "value": f"Bearer {tok}"}]) as s:
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"sandbox__bash", "sandbox__read_file", "sandbox__write_file", "sandbox__ls", "sandbox__glob", "sandbox__grep", "sandbox__str_replace"} <= names

            written = _text(await s.call_tool("sandbox__write_file", {"path": "/mnt/user-data/workspace/probe.txt", "content": "harness was here\n"}))
            assert written["bytes_written"] == len("harness was here\n")

            ran = _text(await s.call_tool("sandbox__bash", {"command": "cat /mnt/user-data/workspace/probe.txt && echo OK"}))
            assert "harness was here" in ran["output"] and "OK" in ran["output"]

            read = _text(await s.call_tool("sandbox__read_file", {"path": "/mnt/user-data/workspace/probe.txt"}))
            assert "harness was here" in read["content"]

            listed = _text(await s.call_tool("sandbox__ls", {"path": "/mnt/user-data/workspace"}))
            assert any("probe.txt" in item["entry"] for item in listed["items"])

        # The file is in the *owner's* thread directory — the one the chat,
        # the Files tab and the native agent all use — not under `default`.
        owner_file = home / "users" / USER / "threads" / THREAD / "user-data" / "workspace" / "probe.txt"
        assert owner_file.read_text() == "harness was here\n"
        assert not (home / "users" / "default").exists()

        # And the Agent's Computer sees it: the Terminal / Files tabs are fed
        # from the thread's sandbox.log, which these calls append to exactly
        # as the lead agent's own tool calls do.
        log = home / "users" / USER / "threads" / THREAD / "sandbox.log"
        observed = [json.loads(line)["type"] for line in log.read_text().splitlines() if line.strip()]
        assert {"write_file", "bash", "read_file"} <= set(observed), observed
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_token_without_a_thread_cannot_touch_any_sandbox(home: Path, tmp_path: Path):
    app, server, tokens = await _gateway(tmp_path)
    try:
        tok = (await tokens.create(owner_user_id=USER, name="long-lived", scopes=["*"]))["token"]
        async with _Mcp(app, "http://nova/api/mcp/nova", [{"name": "Authorization", "value": f"Bearer {tok}"}]) as s:
            res = await s.call_tool("sandbox__bash", {"command": "id"})
            assert res.isError is True
            assert "No thread in scope" in res.content[0].text
            # Naming a thread explicitly is allowed — that is acting on data the caller named.
            ok = _text(await s.call_tool("sandbox__bash", {"command": "echo named", "thread_id": THREAD}))
            assert "named" in ok["output"]
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_runtime_turn_mints_token_and_the_runtime_edits_the_sandbox(home: Path, tmp_path: Path):
    """The whole turn: middleware -> token -> adapter -> Nova MCP -> sandbox -> AIMessage."""
    from deerflow.config.acp_config import ACPAgentConfig
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware
    from deerflow.runtimes.registry import RuntimeRegistry

    app, server, tokens = await _gateway(tmp_path)
    try:
        agents = {"claude_code": ACPAgentConfig(command="npx", args=["-y", "claude-agent-acp"], description="cc")}

        def nova_mcp(user_id, thread_id):
            async def _mint():
                return await tokens.create(owner_user_id=user_id, name=f"runtime:{thread_id}", scopes=["*"])

            return {"__mint__": _mint, "url": "http://nova/api/mcp/nova", "repo": tokens}, lambda: None

        seen: dict[str, Any] = {}

        async def adapter(agent_cfg, prompt, *, cwd, mcp_servers, permission, on_text, on_status, model=None, timeout=None):
            """Stand-in for `npx claude-agent-acp`: uses the MCP server it was
            handed exactly as Claude Code does, then answers."""
            seen["prompt"] = prompt
            nova = next(m for m in mcp_servers if m["name"] == "nova")
            on_status("sess-1", "execute: Bash(echo from-runtime > /mnt/user-data/workspace/out.txt)")
            async with _Mcp(app, nova["url"], nova["headers"]) as s:
                _text(await s.call_tool("sandbox__bash", {"command": "echo from-runtime > /mnt/user-data/workspace/out.txt"}))
                back = _text(await s.call_tool("sandbox__read_file", {"path": "/mnt/user-data/workspace/out.txt"}))
            on_text("sess-1", f"I wrote out.txt; it now reads: {back['content'].strip()}")
            return f"I wrote out.txt; it now reads: {back['content'].strip()}"

        events: list[dict] = []
        mw = RuntimeDispatchMiddleware(registry=RuntimeRegistry(acp_agents=agents, default="native"), run_prompt=adapter, stream_writer=lambda: events.append, nova_mcp=nova_mcp)

        class Req:
            messages = [HumanMessage("create out.txt in my workspace")]
            state = {"messages": messages}

            class runtime:
                context = {"runtime": "claude_code", "permission_mode": "full", "thread_id": THREAD, "user_id": USER}

        async def native(req):
            raise AssertionError("native model must not run when the runtime succeeds")

        result = await mw.awrap_model_call(Req(), native)
        assert isinstance(result, AIMessage)
        assert "from-runtime" in result.content
        assert result.response_metadata["runtime"] == "claude_code"
        assert (home / "users" / USER / "threads" / THREAD / "user-data" / "workspace" / "out.txt").read_text().strip() == "from-runtime"
        assert seen["prompt"].endswith("create out.txt in my workspace")
        kinds = [e["kind"] for e in events if e["type"] == "acp_update"]
        assert "status" in kinds and "text" in kinds
        # The per-turn token was revoked once the turn ended.
        remaining = [t for t in await tokens.list(owner_user_id=USER) if t.get("name") == f"runtime:{THREAD}"]
        assert remaining and all(t.get("revoked_at") for t in remaining), remaining
    finally:
        await server.stop()
