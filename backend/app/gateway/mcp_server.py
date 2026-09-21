"""Nova as an MCP server — ``/api/mcp/nova``.

Every capability operation declared ``mcp=True`` is served as an MCP tool
over the streamable-HTTP transport, so an external harness — Claude Code
through the ACP adapter, OpenClaw, a script — can drive Nova's own
capabilities (jobs, integrations, agents, models, …) with the same
declarations the lead agent and the UI use.

Auth is a **harness token** (``Authorization: Bearer nhk_…``): it pins the
user every call runs as and the capability modules it may reach. There is
no cookie path and therefore no CSRF surface. Admin-only and ``secret``
operations are never listed here regardless of scope.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from deerflow.capabilities import CapabilityRegistry, OpContext, Operation, OperationNotFound
from deerflow.capabilities.modules.features import compute_flags
from deerflow.persistence.harness_token.sql import HarnessTokenRepository

logger = logging.getLogger(__name__)

MOUNT_PATH = "/api/mcp/nova"


#: Per-turn runtime tokens are minted as ``runtime:<thread_id>`` by
#: ``RuntimeDispatchMiddleware``. Parsing it back is what lets an MCP caller
#: act on the thread it was minted for.
_RUNTIME_TOKEN_PREFIX = "runtime:"


def _thread_from_token_name(name: str | None) -> str | None:
    """The thread a per-turn runtime token was minted for, if any.

    Without this an MCP caller has no thread: ``OpContext.thread_id`` stayed
    ``None``, so every thread-scoped capability — anything touching the
    Agent's Computer — had nothing to act on. That is the concrete reason the
    ACP path could manage Nova but never drive its sandbox, while the native
    lead agent (which always has the thread) could.

    A long-lived token minted by a human has an arbitrary name and yields
    ``None``, which is correct: it is not bound to any one thread.
    """
    if not name or not name.startswith(_RUNTIME_TOKEN_PREFIX):
        return None
    thread_id = name[len(_RUNTIME_TOKEN_PREFIX) :].strip()
    if not thread_id or thread_id == "chat":
        return None
    return thread_id


@dataclass(frozen=True)
class Principal:
    user_id: str
    scopes: frozenset[str]
    #: Set only for a per-turn runtime token; None for a long-lived one.
    thread_id: str | None = None

    def allows(self, module_id: str) -> bool:
        return "*" in self.scopes or module_id in self.scopes


_principal: ContextVar[Principal | None] = ContextVar("nova_mcp_principal", default=None)


def _tool_for(op: Operation) -> types.Tool:
    return types.Tool(
        name=op.tool_name,
        title=op.name,
        description=f"[{op.kind}] {op.description}",
        inputSchema=op.input.model_json_schema(),
        outputSchema=op.output.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=op.kind == "read", destructiveHint=op.kind in ("write", "execute"), title=op.name),
    )


class NovaMcpServer:
    def __init__(self, registry: CapabilityRegistry, tokens: HarnessTokenRepository | None, *, flags: Callable[[], dict[str, bool]] = compute_flags) -> None:
        self.registry = registry
        self.tokens = tokens
        self._flags = flags
        self._server: Server = Server("nova")
        self._manager: StreamableHTTPSessionManager | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._stopping = asyncio.Event()
        self._register_handlers()

    # -- MCP handlers -----------------------------------------------------

    def _visible_ops(self, principal: Principal) -> list[Operation]:
        flags = {k for k, v in self._flags().items() if v}
        return [op for op in self.registry.operations(enabled_flags=flags) if op.mcp and not op.admin_only and op.kind != "secret" and principal.allows(op.module_id)]

    def _register_handlers(self) -> None:
        @self._server.list_tools()
        async def _list_tools() -> list[types.Tool]:
            principal = _principal.get()
            if principal is None:
                return []
            return [_tool_for(op) for op in self._visible_ops(principal)]

        @self._server.call_tool(validate_input=False)
        async def _call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
            principal = _principal.get()
            if principal is None:
                return _error("not authenticated")
            op_name = name.replace("__", ".", 1)
            try:
                op = self.registry.get(op_name)
            except OperationNotFound:
                return _error(f"unknown tool {name!r}")
            if op not in self._visible_ops(principal):
                return _error(f"tool {name!r} is not available to this token")
            ctx = OpContext(
                user_id=principal.user_id,
                is_admin=False,
                thread_id=principal.thread_id,
                surface="mcp",
                extras={"scopes": sorted(principal.scopes)},
            )
            try:
                result = await self.registry.invoke(op_name, ctx, arguments or {})
            except Exception as exc:
                return _error(f"{type(exc).__name__}: {exc}")
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))], structuredContent=result, isError=False)

    # -- transport --------------------------------------------------------

    def mount(self, app: FastAPI) -> None:
        self._manager = StreamableHTTPSessionManager(app=self._server, json_response=True, stateless=True)
        # An exact Route, not a Mount: a Mount 307-redirects the bare path to
        # a trailing slash, which MCP clients do not follow for POST.
        handler = _AuthedASGI(self)
        app.router.routes.append(Route(MOUNT_PATH, endpoint=handler, methods=["GET", "POST", "DELETE"]))
        app.router.routes.append(Route(MOUNT_PATH + "/", endpoint=handler, methods=["GET", "POST", "DELETE"]))
        app.state.nova_mcp_server = self

    @property
    def running(self) -> bool:
        return self._task is not None and self._started.is_set() and not self._stopping.is_set()

    async def start(self) -> None:
        """Run the session manager's task group for the app's lifetime.

        The manager is an anyio task group, which must be entered and exited
        by the same task — so it lives in one dedicated task rather than an
        exit stack unwound from the lifespan's teardown."""
        if self._manager is None or self._task is not None:
            return

        async def _serve() -> None:
            async with self._manager.run():  # type: ignore[union-attr]
                self._started.set()
                await self._stopping.wait()

        self._task = asyncio.create_task(_serve(), name="nova-mcp-server")
        await self._started.wait()
        logger.info("Nova MCP server listening at %s", MOUNT_PATH)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._task, timeout=5)
        self._task = None
        self._started = asyncio.Event()
        self._stopping = asyncio.Event()

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert self._manager is not None
        await self._manager.handle_request(scope, receive, send)

    async def authenticate(self, authorization: str | None) -> Principal | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        if self.tokens is None:
            return None
        resolved = await self.tokens.resolve(authorization[7:].strip())
        if resolved is None:
            return None
        return Principal(
            user_id=str(resolved["owner_user_id"]),
            scopes=frozenset(resolved["scopes"] or ["*"]),
            thread_id=_thread_from_token_name(resolved.get("name")),
        )


class _AuthedASGI:
    def __init__(self, server: NovaMcpServer) -> None:
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        principal = await self.server.authenticate(headers.get("authorization"))
        if principal is None:
            response = JSONResponse({"detail": "harness token required"}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="nova-mcp"'})
            await response(scope, receive, send)
            return
        if not self.server.running:
            response = JSONResponse({"detail": "MCP server not started"}, status_code=503)
            await response(scope, receive, send)
            return
        token = _principal.set(principal)
        try:
            await self.server.handle(scope, receive, send)
        finally:
            _principal.reset(token)


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)
