"""RuntimeDispatchMiddleware — run a chat turn on Claude Code / OpenClaw.

Sits in the lead agent's middleware chain. On ``awrap_model_call`` it
reads the selected runtime (thread override > model > default); for
``native`` it is a no-op. Otherwise it turns the conversation into one
ACP prompt, runs it through the adapter with Nova's own MCP server mounted
(so the external agent has every Nova capability), streams the transcript
as ``acp_update`` events, and returns the agent's answer as the
``AIMessage`` — threads, checkpoints, titles and memory see an ordinary
turn.

When the runtime cannot finish the turn — observed live on 2026-09-21 as
``Internal error: You've hit your limit · resets 3am (UTC)`` from the
Claude Code adapter, five minutes after the user hit send — the turn is
handed to the native handler instead of being answered with the error
(``fallback_to_native``, default on). The native agent has the full tool
suite (sandbox, subagents, skills), so the user gets an answer from the
same message rather than a sentence telling them to try again later.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from nova.runtimes.registry import RuntimeRegistry, get_runtime_registry
from nova.runtimes.session import NARRATION_PREFIXES, NovaMcpFactory, default_nova_mcp, mirror_runtime_action, open_acp_session
from nova.runtimes.transcript import transcript_for_prompt
from nova.runtimes.types import NATIVE, policy_for_mode

logger = logging.getLogger(__name__)

RunPrompt = Callable[..., Any]


def _default_stream_writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return None


_default_nova_mcp = default_nova_mcp


def _usage_metadata_for(text: Any) -> dict[str, int] | None:
    """The turn's usage in LangChain's ``usage_metadata`` shape.

    ``None`` when the adapter reported no usage — a message without usage
    metadata is what every non-ACP turn looks like, so this keeps the ACP
    path indistinguishable from the native one when there is nothing to say.
    """
    usage = getattr(text, "usage", None)
    if usage is None:
        return None
    total = int(getattr(usage, "total_tokens", 0) or 0)
    if total <= 0:
        return None
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": total,
    }


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
        fallback_to_native: bool = True,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._run_prompt = run_prompt
        self._stream_writer = stream_writer
        self._nova_mcp = nova_mcp
        self._model_runtime = model_runtime
        self._turn_timeout = turn_timeout
        self._fallback_to_native = fallback_to_native

    @property
    def registry(self) -> RuntimeRegistry:
        return self._registry or get_runtime_registry()

    def _context(self, request: Any) -> dict[str, Any]:
        runtime = getattr(request, "runtime", None)
        ctx = getattr(runtime, "context", None) or {}
        return dict(ctx) if isinstance(ctx, dict) else {}

    def _record_turn_usage(self, ctx: dict[str, Any], runtime_name: str, agent_cfg: Any, text: Any) -> None:
        """Report the ACP turn's token usage to the run's journal.

        An ACP turn never passes through an LLM callback, so the journal's
        ``on_llm_end`` path cannot see it: the whole turn's tokens were
        missing from the run's totals and from the per-model breakdown
        (cost accounting read "this run cost nothing" for a turn that spent
        200k tokens on Claude Code).

        The usage rides on the turn's text (see ``ACPText``). Recorded as an
        external usage record so the tokens land in the lead-agent bucket of
        the run and in a per-model bucket named after the runtime. Never
        raises: cost reporting must not be able to fail the turn it is
        reporting on.
        """
        try:
            usage = getattr(text, "usage", None)
            if usage is None or usage.total_tokens <= 0:
                return
            journal = ctx.get("__run_journal")
            if journal is None:
                return
            model_name = getattr(agent_cfg, "model", None) or f"acp:{runtime_name}"
            journal.record_external_llm_usage_records(
                [
                    {
                        # Stable per turn, so a re-reported turn is ignored
                        # by the journal's source-id dedup instead of
                        # double-counting. Without a session id the fallback
                        # is a fresh uuid, never ``id(text)``: CPython reuses
                        # ids after GC, so a later turn could collide with an
                        # earlier one and be silently dropped as a duplicate.
                        "source_run_id": f"acp:{runtime_name}:{getattr(text, 'session_id', '') or uuid.uuid4().hex}",
                        "caller": "lead_agent",
                        "model_name": model_name,
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                ]
            )
        except Exception:
            logger.debug("Failed to record ACP turn usage (non-fatal)", exc_info=True)

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
                from nova.runtime.user_context import get_effective_user_id

                user_id = get_effective_user_id()
            except Exception:
                user_id = None

        def emit(session_id: str, kind: str, delta: str) -> None:
            if not delta:
                return
            if kind == "status":
                _mirror_runtime_action(thread_id, selection.runtime, delta)
            if writer is None:
                return
            try:
                writer({"type": "acp_update", "agent": selection.runtime, "session_id": session_id or str(thread_id or ""), "kind": kind, "delta": delta})
            except Exception:
                logger.debug("acp_update emit failed", exc_info=True)

        prompt = transcript_for_prompt(list(getattr(request, "messages", None) or []))
        try:
            async with open_acp_session(agent=selection.runtime, user_id=user_id, thread_id=thread_id, nova_mcp=self._nova_mcp, turn_timeout=self._turn_timeout) as session:
                run = self._run_prompt
                if run is None:
                    from nova.runtimes.acp_transport import run_acp_prompt

                    run = run_acp_prompt
                cwd = _work_dir(thread_id)
                emit("", "status", f"runtime: {selection.runtime} ({permission.label})")
                text = await session.run(run, agent_cfg, prompt, cwd=cwd, permission=permission, on_text=lambda sid, d: emit(sid, "text", d), on_status=lambda sid, s: emit(sid, "status", s))
            # The ACP turn's tokens are not an LLM callback, so nothing else
            # reports them: without this an entire Claude Code turn costs
            # nothing in the run's totals and nothing in the per-model cost
            # breakdown.
            self._record_turn_usage(ctx, selection.runtime, agent_cfg, text)
            # Also on the message itself, so the token middleware's log line
            # and any consumer reading the message stream see the turn's
            # real usage instead of a zero-usage message.
            usage_metadata = _usage_metadata_for(text)
            return AIMessage(
                content=text or "(no response)",
                usage_metadata=usage_metadata,
                response_metadata={"runtime": selection.runtime, "account": selection.account, "permission_mode": selection.permission_mode},
            )
        except Exception as exc:
            reason = _failure_reason(exc)
            if not self._fallback_to_native:
                logger.error("runtime %s failed: %s", selection.runtime, reason)
                emit("", "status", f"runtime error: {reason}")
                return AIMessage(content=f"The {selection.runtime} runtime could not complete this turn: {reason}", response_metadata={"runtime": selection.runtime, "runtime_error": True})
            # Same turn, native agent, full tool suite. The handler sees the
            # request exactly as every earlier middleware shaped it.
            logger.warning("runtime %s failed: %s — falling back to the native agent for this turn", selection.runtime, reason)
            emit("", "status", f"runtime {selection.runtime} unavailable ({reason}) — continuing on the native Nova agent (sandbox + subagents)")
            result = await handler(request)
            _mark_fallback(result, selection.runtime, reason)
            return result


# The session module owns these now; the names stay importable from here.
_NARRATION_PREFIXES = NARRATION_PREFIXES
_mirror_runtime_action = mirror_runtime_action


def _failure_reason(exc: BaseException) -> str:
    """One line for logs, status events and the fallback metadata."""
    if isinstance(exc, TimeoutError):
        return "TimeoutError: the runtime did not answer within the turn budget"
    text = str(exc).strip() or exc.__class__.__name__
    return f"{type(exc).__name__}: {text}"


def _mark_fallback(result: Any, runtime: str, reason: str) -> None:
    """Record on the native reply which runtime was asked first and why it
    was skipped, so the transcript and the UI can say so. Best effort: the
    handler may hand back something other than an AIMessage."""
    meta = getattr(result, "response_metadata", None)
    if isinstance(meta, dict):
        meta.setdefault("runtime", NATIVE)
        meta["runtime_fallback_from"] = runtime
        meta["runtime_fallback_reason"] = reason


def _work_dir(thread_id: str | None) -> str:
    try:
        from nova.tools.builtins.invoke_acp_agent_tool import _get_work_dir

        return _get_work_dir(thread_id)
    except Exception:
        import os

        return os.getcwd()
