"""Nova as an MCP server: every ``mcp=True`` capability operation is a tool
an external harness (Claude Code, OpenClaw) can call, authenticated by a
harness token that pins the user and the modules it may reach.

Exercised end to end with the real MCP client over the streamable-HTTP
transport, against the ASGI app in-process (no sockets).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from deerflow.capabilities import CapabilityModule, CapabilityRegistry, ModuleStatus, OpContext, Operation

pytestmark = pytest.mark.no_auto_user


class In(BaseModel):
    n: int = 1


class Out(BaseModel):
    user: str
    n: int


async def _double(ctx: OpContext, inp: In) -> Out:
    assert ctx.surface == "mcp"
    return Out(user=ctx.user_id, n=inp.n * 2)


async def _status() -> ModuleStatus:
    return ModuleStatus(configured=True, healthy=True)


def _registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(
        CapabilityModule(
            id="demo",
            title="Demo",
            status=_status,
            operations=[
                Operation(name="demo.double", kind="read", input=In, output=Out, handler=_double, description="Double n"),
                Operation(name="demo.hidden", kind="read", input=In, output=Out, handler=_double, description="not on mcp", mcp=False),
                Operation(name="demo.admin", kind="admin", input=In, output=Out, handler=_double, description="admin", admin_only=True),
            ],
        )
    )
    reg.register(
        CapabilityModule(
            id="other",
            title="Other",
            status=_status,
            operations=[
                Operation(name="other.double", kind="read", input=In, output=Out, handler=_double, description="Double n"),
            ],
        )
    )
    return reg


async def _tokens(tmp_path):
    from deerflow.persistence.engine import get_session_factory, init_engine
    from deerflow.persistence.harness_token.sql import HarnessTokenRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}", sqlite_dir=str(tmp_path))
    return HarnessTokenRepository(get_session_factory())


@pytest.fixture(autouse=True)
def _close_engine_after_test():
    yield
    import asyncio

    from deerflow.persistence.engine import close_engine

    asyncio.run(close_engine())


class _Session:
    """MCP ClientSession over the in-process ASGI app."""

    def __init__(self, app: FastAPI, token: str | None):
        self.app, self.token = app, token

    async def __aenter__(self):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://nova", headers=headers, timeout=10)
        self._cm = streamable_http_client("http://nova/api/mcp/nova", http_client=self._http)
        read, write, _ = await self._cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, *exc):
        await self._session_cm.__aexit__(*exc)
        await self._cm.__aexit__(*exc)
        await self._http.aclose()


async def _app(tmp_path, *, registry=None):
    from app.gateway.mcp_server import NovaMcpServer

    tokens = await _tokens(tmp_path)
    server = NovaMcpServer(registry or _registry(), tokens, flags=lambda: {})
    app = FastAPI()
    server.mount(app)
    await server.start()
    return app, server, tokens


@pytest.mark.anyio
async def test_lists_only_mcp_ops_within_scope_and_calls_as_the_token_owner(tmp_path):
    app, server, tokens = await _app(tmp_path)
    try:
        tok = (await tokens.create(owner_user_id="u1", name="cc", scopes=["demo"]))["token"]
        async with _Session(app, tok) as s:
            names = sorted(t.name for t in (await s.list_tools()).tools)
            assert names == ["demo__double"]  # hidden (mcp=False), admin-only and out-of-scope `other` are absent
            res = await s.call_tool("demo__double", {"n": 21})
            assert res.isError is False
            assert res.structuredContent == {"user": "u1", "n": 42}
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_wildcard_scope_sees_every_module(tmp_path):
    app, server, tokens = await _app(tmp_path)
    try:
        tok = (await tokens.create(owner_user_id="u1", name="all", scopes=["*"]))["token"]
        async with _Session(app, tok) as s:
            names = sorted(t.name for t in (await s.list_tools()).tools)
            assert names == ["demo__double", "other__double"]
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_tool_errors_are_reported_not_raised(tmp_path):
    app, server, tokens = await _app(tmp_path)
    try:
        tok = (await tokens.create(owner_user_id="u1", name="cc", scopes=["*"]))["token"]
        async with _Session(app, tok) as s:
            res = await s.call_tool("demo__double", {"n": "x"})
            assert res.isError is True
            assert "validation" in res.content[0].text.lower() or "n" in res.content[0].text
            res = await s.call_tool("nope__nothing", {})
            assert res.isError is True
    finally:
        await server.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "nhk_" + "x" * 43, "not-a-token"])
async def test_missing_or_bad_token_is_401(tmp_path, token):
    app, server, _ = await _app(tmp_path)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nova") as c:
            res = await c.post("/api/mcp/nova", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers={**headers, "Accept": "application/json, text/event-stream"})
            assert res.status_code == 401
            assert res.headers.get("www-authenticate", "").startswith("Bearer")
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_revoked_token_stops_working_immediately(tmp_path):
    app, server, tokens = await _app(tmp_path)
    try:
        created = await tokens.create(owner_user_id="u1", name="cc", scopes=["*"])
        async with _Session(app, created["token"]) as s:
            assert (await s.list_tools()).tools
        await tokens.revoke(created["id"], owner_user_id="u1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nova") as c:
            res = await c.post("/api/mcp/nova", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers={"Authorization": f"Bearer {created['token']}", "Accept": "application/json, text/event-stream"})
            assert res.status_code == 401
    finally:
        await server.stop()


def test_only_the_server_path_is_public_and_csrf_exempt():
    """/api/mcp/nova does its own bearer auth; the MCP *client* routers
    (/api/mcp/config, /api/mcp/cache/reset) must stay behind the session."""
    from app.gateway.auth_middleware import _is_public
    from app.gateway.csrf_middleware import is_csrf_exempt_path

    assert _is_public("/api/mcp/nova") and _is_public("/api/mcp/nova/")
    assert not _is_public("/api/mcp/config") and not _is_public("/api/mcp/cache/reset")
    assert is_csrf_exempt_path("/api/mcp/nova")
    assert not is_csrf_exempt_path("/api/mcp/config")
