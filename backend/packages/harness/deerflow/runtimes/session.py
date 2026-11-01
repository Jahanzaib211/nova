"""One ACP session for every way Nova runs an external agent.

Nova reaches Claude Code / OpenClaw two ways: as the runtime of a chat
(``RuntimeDispatchMiddleware``) and as a subtask the lead agent hands out
(``invoke_acp_agent``). Both need the same three things around the ACP
prompt:

- **hands** -- Nova's MCP server mounted with an ephemeral harness token, so
  the agent's shell and file work lands in the user's sandbox, followed by
  the operator's MCP client servers;
- **a briefing** -- where those hands are, and that permission requests are
  answered by policy, not by a person;
- **a mirror** -- the agent's own tool calls and permission decisions written
  into the thread's ``sandbox.log`` so the Agent's Computer shows them;
- **a deadline** -- one wall-clock budget per prompt. The chat path passed
  ``runtimes.turn_timeout_seconds``; delegation passed nothing, so a wedged
  adapter held the lead agent's tool call (and the run) open indefinitely.

They used to be built separately, and the delegation path had none of them:
on 2026-11-01 a delegated Claude Code had no way into the user's sandbox,
its private ``Write`` was denied, and it asked the user to approve a prompt
nobody was shown. ``open_acp_session`` is now the only place either path
gets them, so a fix to one cannot skip the other.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from deerflow.runtimes.transcript import CHAT_ROLE, permission_contract, runtime_preamble

logger = logging.getLogger(__name__)

NovaMcpFactory = Callable[[str | None, str | None], tuple[dict[str, Any] | None, Callable[[], Any]]]
ClientServers = Callable[[], list[dict[str, Any]]]

#: Status lines that narrate rather than act -- never mirrored into the log.
NARRATION_PREFIXES = ("thinking:", "runtime")

#: Matches ``RuntimesConfig.turn_timeout_seconds``' default.
DEFAULT_TURN_TIMEOUT = 1800.0
#: How long past the budget the session waits for a runner that ignores its
#: own ``timeout`` argument before cancelling it from outside.
TIMEOUT_GRACE_S = 2.0


class ACPTurnTimeout(TimeoutError):
    """An ACP prompt overran the session's budget. A ``TimeoutError``, so the
    chat runtime's fallback-to-native path treats it as it always has."""


def default_turn_timeout() -> float:
    """``runtimes.turn_timeout_seconds``, or the default when config is unreadable."""
    try:
        from deerflow.config.app_config import get_app_config

        value = getattr(getattr(get_app_config(), "runtimes", None), "turn_timeout_seconds", None)
        return float(value) if value else DEFAULT_TURN_TIMEOUT
    except Exception:
        return DEFAULT_TURN_TIMEOUT


def default_nova_mcp(user_id: str | None, thread_id: str | None) -> tuple[dict[str, Any] | None, Callable[[], Any]]:
    """Describe Nova's MCP server with a lazy token mint; the returned
    callable is a cleanup hook (the token itself is revoked by the session)."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    if sf is None or not user_id:
        return None, lambda: None
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.harness_token.sql import HarnessTokenRepository

    rt = getattr(get_app_config(), "runtimes", None)
    url = getattr(rt, "nova_mcp_url", None) or "http://127.0.0.1:8001/api/mcp/nova"
    repo = HarnessTokenRepository(sf)

    async def _mint():
        return await repo.create(owner_user_id=user_id, name=f"runtime:{thread_id or 'chat'}", scopes=["*"])

    return {"__mint__": _mint, "url": url, "repo": repo}, lambda: None


def default_client_servers() -> list[dict[str, Any]]:
    from deerflow.runtimes.acp_transport import build_acp_mcp_servers

    return build_acp_mcp_servers()


def mirror_runtime_action(thread_id: str | None, agent: str, status: str) -> None:
    """Write one of the agent's own tool calls / permission decisions into
    the thread's sandbox.log so the Agent's Computer shows it as an action.

    ``acp_transport`` formats tool calls as ``"<kind>: <title>"`` and
    permission decisions as ``"permission approved|denied: <kind> — <title>"``.
    Calls the agent makes *through* Nova's ``sandbox__*`` tools are logged
    by those tools already; they surface here only as the ACP-side title
    (``other: mcp__nova__sandbox__bash``) and are skipped so a single command
    is not two cards.
    """
    if not thread_id or status.startswith(NARRATION_PREFIXES):
        return
    if "mcp__nova__" in status:
        return
    head, sep, title = status.partition(": ")
    if not sep:
        return
    kind = head
    if head.startswith("permission "):
        # "permission denied: execute — rm -rf" -> kind "permission_denied", title "execute — rm -rf"
        kind = head.replace(" ", "_")
    try:
        from deerflow.sandbox.tools import record_runtime_observation

        record_runtime_observation(thread_id, agent=agent, kind=kind, title=title)
    except Exception:  # never let the mirror break the turn
        logger.debug("runtime action mirror failed", exc_info=True)


@dataclass
class ACPSession:
    """What one ACP prompt is run with. Built by :func:`open_acp_session`."""

    agent: str
    thread_id: str | None
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    nova_mounted: bool = False
    preamble: str = ""
    turn_timeout: float | None = DEFAULT_TURN_TIMEOUT

    def brief(self, prompt: str) -> str:
        """The prompt the agent receives: briefing first, then the task."""
        return f"{self.preamble}\n\n---\n\n{prompt}" if self.preamble else prompt

    def mirror(self, status: str) -> None:
        """Mirror one status line (tool call / permission decision) into sandbox.log."""
        mirror_runtime_action(self.thread_id, self.agent, status)

    async def run(self, run_prompt: Callable[..., Any], agent_cfg: Any, prompt: str, *, cwd: str, permission: Any, on_text: Callable[[str, str], None], on_status: Callable[[str, str], None], model: str | None = None) -> str:
        """Run one prompt with this session's servers, briefing and deadline.

        The runner gets the budget as ``timeout`` (``run_acp_prompt`` enforces
        it and reaps the adapter); the session also guards from outside, so a
        runner that ignores the argument is still bounded. Either way the
        overrun surfaces as :class:`ACPTurnTimeout` naming agent and budget.
        """
        call = run_prompt(agent_cfg, self.brief(prompt), cwd=cwd, mcp_servers=self.mcp_servers, permission=permission, on_text=on_text, on_status=on_status, model=model, timeout=self.turn_timeout)
        try:
            if not self.turn_timeout:
                return await call
            return await asyncio.wait_for(call, timeout=self.turn_timeout + TIMEOUT_GRACE_S)
        except ACPTurnTimeout:
            raise
        except TimeoutError as exc:
            budget = f"{self.turn_timeout:.0f}s" if self.turn_timeout else "its budget"
            raise ACPTurnTimeout(f"{self.agent} did not finish within {budget} (the ACP turn budget, runtimes.turn_timeout_seconds); the adapter was stopped") from exc


@contextlib.asynccontextmanager
async def open_acp_session(
    *,
    agent: str,
    user_id: str | None,
    thread_id: str | None,
    role: str = CHAT_ROLE,
    nova_mcp: NovaMcpFactory = default_nova_mcp,
    client_servers: ClientServers = default_client_servers,
    degrade_on_mount_error: bool = False,
    turn_timeout: float | None = None,
) -> AsyncIterator[ACPSession]:
    """Mount Nova's server, add the client servers, compose the briefing.

    A failure to mount Nova's server (factory or token mint) raises by
    default -- the chat runtime falls back to the native agent, which has the
    sandbox natively. ``degrade_on_mount_error`` continues without it instead,
    for a delegated agent that can still work in its own workspace. An invalid
    client server configuration is never fatal: it is logged and skipped.
    The ephemeral token is revoked when the session closes, however it closes.
    ``turn_timeout`` defaults to ``runtimes.turn_timeout_seconds``.
    """
    session = ACPSession(agent=agent, thread_id=thread_id, turn_timeout=turn_timeout if turn_timeout is not None else default_turn_timeout())
    nova_server: dict[str, Any] | None = None
    cleanup: Callable[[], Any] = lambda: None  # noqa: E731
    minted: dict[str, Any] | None = None
    try:
        try:
            nova_server, cleanup = nova_mcp(user_id, thread_id)
            if nova_server and "__mint__" in nova_server:
                minted = await nova_server["__mint__"]()
                session.mcp_servers.append({"name": "nova", "type": "http", "url": nova_server["url"], "headers": [{"name": "Authorization", "value": f"Bearer {minted['token']}"}]})
            elif nova_server:
                session.mcp_servers.append(nova_server)
        except Exception as exc:
            if not degrade_on_mount_error:
                raise
            logger.warning("ACP %s: Nova's MCP server could not be mounted; the agent works without the user's sandbox: %s", agent, exc)
            session.mcp_servers.clear()
        session.nova_mounted = any(m.get("name") == "nova" for m in session.mcp_servers)

        try:
            session.mcp_servers.extend(client_servers())
        except Exception as exc:
            logger.warning("ACP %s: invalid MCP server configuration; continuing without MCP servers: %s", agent, exc)

        names = [str(m.get("name", "")) for m in session.mcp_servers]
        session.preamble = "\n\n".join(part for part in (runtime_preamble(names, role=role), permission_contract(nova_mounted=session.nova_mounted)) if part)
        yield session
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
