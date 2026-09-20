"""RuntimeDispatchMiddleware — run a chat turn on Claude Code / OpenClaw.

Sits in the lead agent's middleware chain. On ``awrap_model_call`` it
reads the selected runtime (thread override > model > default); for
``native`` it is a no-op. Otherwise it turns the conversation into one
ACP prompt, runs it through the adapter with Nova's own MCP server mounted
(so the external agent has every Nova capability), streams the transcript
as ``acp_update`` events, and returns the agent's answer as the
``AIMessage`` — threads, checkpoints, titles and memory see an ordinary
turn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from deerflow.runtimes.registry import RuntimeRegistry, get_runtime_registry
from deerflow.runtimes.transcript import transcript_for_prompt
from deerflow.runtimes.types import NATIVE, policy_for_mode

logger = logging.getLogger(__name__)

RunPrompt = Callable[..., Any]
NovaMcpFactory = Callable[[str | None, str | None], tuple[dict[str, Any] | None, Callable[[], Any]]]


def _default_stream_writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return None


def _default_nova_mcp(user_id: str | None, thread_id: str | None) -> tuple[dict[str, Any] | None, Callable[[], Any]]:
    """Mint an ephemeral harness token and describe Nova's MCP server for
    ``new_session``; the returned callable revokes the token afterwards."""
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    rt = getattr(get_app_config(), "runtimes", None)
    url = getattr(rt, "nova_mcp_url", None) or "http://127.0.0.1:8001/api/mcp/nova"
    if sf is None or not user_id:
        return None, lambda: None
    from deerflow.persistence.harness_token.sql import HarnessTokenRepository

    repo = HarnessTokenRepository(sf)

    async def _mint():
        return await repo.create(owner_user_id=user_id, name=f"runtime:{thread_id or 'chat'}", scopes=["*"])

    return {"__mint__": _mint, "url": url, "repo": repo}, lambda: None


class RuntimeDispatchMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        registry: RuntimeRegistry | None = None,
        run_prompt: RunPrompt | None = None,
        stream_writer: Callable[[], Any] = _default_stream_writer,
        nova_mcp: NovaMcpFactory = _default_nova_mcp,
        model_runtime: str | None = None,
        turn_timeout: float | None = 1800.0,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._run_prompt = run_prompt
        self._stream_writer = stream_writer
        self._nova_mcp = nova_mcp
        self._model_runtime = model_runtime
        self._turn_timeout = turn_timeout

    @property
    def registry(self) -> RuntimeRegistry:
        return self._registry or get_runtime_registry()

    def _context(self, request: Any) -> dict[str, Any]:
        runtime = getattr(request, "runtime", None)
        ctx = getattr(runtime, "context", None) or {}
        return dict(ctx) if isinstance(ctx, dict) else {}

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        ctx = self._context(request)
        selection = self.registry.select(context=ctx, model_runtime=self._model_runtime)
        if selection.runtime == NATIVE:
            return await handler(request)

        agent_cfg = self.registry.agent(selection.runtime)
        if agent_cfg is None:
            return await handler(request)

        writer = self._stream_writer()
        permission = policy_for_mode(selection.permission_mode)
        thread_id = ctx.get("thread_id")
        user_id = ctx.get("user_id")
        if not user_id:
            try:
                from deerflow.runtime.user_context import get_effective_user_id

                user_id = get_effective_user_id()
            except Exception:
                user_id = None

        def emit(session_id: str, kind: str, delta: str) -> None:
            if writer is None or not delta:
                return
            try:
                writer({"type": "acp_update", "agent": selection.runtime, "session_id": session_id or str(thread_id or ""), "kind": kind, "delta": delta})
            except Exception:
                logger.debug("acp_update emit failed", exc_info=True)

        prompt = transcript_for_prompt(list(getattr(request, "messages", None) or []))
        nova_server, cleanup = self._nova_mcp(user_id, thread_id)
        mcp_servers: list[dict[str, Any]] = []
        minted: dict[str, Any] | None = None
        try:
            if nova_server and "__mint__" in nova_server:
                minted = await nova_server["__mint__"]()
                mcp_servers.append({"name": "nova", "type": "http", "url": nova_server["url"], "headers": [{"name": "Authorization", "value": f"Bearer {minted['token']}"}]})
            elif nova_server:
                mcp_servers.append(nova_server)
            try:
                from deerflow.runtimes.acp_transport import build_acp_mcp_servers

                mcp_servers.extend(build_acp_mcp_servers())
            except Exception as exc:
                logger.warning("runtime %s: MCP client servers skipped: %s", selection.runtime, exc)

            run = self._run_prompt
            if run is None:
                from deerflow.runtimes.acp_transport import run_acp_prompt

                run = run_acp_prompt
            cwd = _work_dir(thread_id)
            emit("", "status", f"runtime: {selection.runtime} ({permission.label})")
            text = await run(agent_cfg, prompt, cwd=cwd, mcp_servers=mcp_servers, permission=permission, on_text=lambda sid, d: emit(sid, "text", d), on_status=lambda sid, s: emit(sid, "status", s), model=None, timeout=self._turn_timeout)
            return AIMessage(content=text or "(no response)", response_metadata={"runtime": selection.runtime, "account": selection.account, "permission_mode": selection.permission_mode})
        except Exception as exc:
            logger.error("runtime %s failed: %s", selection.runtime, exc)
            emit("", "status", f"runtime error: {type(exc).__name__}: {exc}")
            return AIMessage(content=f"The {selection.runtime} runtime could not complete this turn: {type(exc).__name__}: {exc}", response_metadata={"runtime": selection.runtime, "runtime_error": True})
        finally:
            if minted is not None and nova_server and nova_server.get("repo") is not None:
                try:
                    await nova_server["repo"].revoke(minted["id"], owner_user_id=str(user_id))
                except Exception:
                    logger.debug("ephemeral harness token revoke failed", exc_info=True)
            try:
                cleanup()
            except Exception:
                pass


def _work_dir(thread_id: str | None) -> str:
    try:
        from deerflow.tools.builtins.invoke_acp_agent_tool import _get_work_dir

        return _get_work_dir(thread_id)
    except Exception:
        import os

        return os.getcwd()
