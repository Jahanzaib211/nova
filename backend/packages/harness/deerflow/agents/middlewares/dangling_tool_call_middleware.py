"""Middleware to fix dangling tool calls in message history.

A dangling tool call occurs when an AIMessage contains tool_calls but there are
no corresponding ToolMessages in the history (e.g., due to user interruption or
request cancellation). This causes LLM errors due to incomplete message format.

The repair is applied at two levels:

* ``before_model`` — the **state-level** repair (the primary one). The
  normalised history is written back to the agent state, so the repair lives in
  the checkpoint. Every later model call of the run then reads an already-clean
  history and has nothing left to patch. This is what stops the middleware from
  re-detecting (and re-logging) the same dangling call on every model call of a
  run, and it stops the dangling calls from accumulating turn after turn.

* ``wrap_model_call`` — the **transient** safety net. The model-bound request
  is patched in place without touching the checkpoint, so a history that
  reaches the model un-normalised (for example one re-shaped by a later
  middleware such as summarization) is still well-formed for the provider.

Ordering note (why the state repair needs a sentinel):
``before_model`` returning ``{"messages": [tool_message]}`` cannot place the
synthetic ToolMessage right after the AIMessage that declared the call: the
``add_messages`` reducer *appends* unknown messages to the end of the list
(``langgraph.graph.message.add_messages`` merge branch). The
``REMOVE_ALL_MESSAGES`` sentinel is used instead — the reducer then returns the
replacement list verbatim (``return right[remove_all_idx + 1 :]``), so the
exact order is preserved and the ToolMessage lands directly after its
AIMessage. This is the same mechanism ``SummarizationMiddleware`` uses to
replace the message list.
"""

import json
import logging
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# Workaround for issue #2894: malformed write_file calls can carry huge Markdown
# payloads in invalid tool-call args. Keep recovery error details short so the
# synthetic ToolMessage does not echo large or malformed content back to the model.
_MAX_RECOVERY_ERROR_DETAIL_LEN = 500


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Inserts placeholder ToolMessages for dangling tool calls before model invocation.

    Scans the message history for AIMessages whose tool_calls lack corresponding
    ToolMessages, and injects synthetic error responses immediately after the
    offending AIMessage so the LLM receives a well-formed conversation.

    The repair is persisted into the agent state by ``before_model`` so it is
    applied once per run instead of once per model call; ``wrap_model_call``
    keeps a transient safety net for histories that reach the model un-normalised.
    """

    # This middleware instance is created once per agent graph and is therefore
    # shared by every run that graph serves. The "already reported" bookkeeping
    # below is consequently keyed by (thread_id, run_id) and bounded in both
    # dimensions so a long-lived process cannot grow it without limit.
    _MAX_TRACKED_RUNS = 64
    _MAX_REPORTED_IDS_PER_RUN = 256

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        # (thread_id, run_id) -> ordered set of tool-call ids already reported.
        self._reported_ids: dict[tuple[str, str], dict[str, None]] = {}

    @staticmethod
    def _message_tool_calls(msg) -> list[dict]:
        """Return normalized tool calls from structured fields or raw provider payloads.

        LangChain stores malformed provider function calls in ``invalid_tool_calls``.
        They do not execute, but provider adapters may still serialize enough of the
        call id/name back into the next request that strict OpenAI-compatible
        validators expect a matching ToolMessage. Treat them as dangling calls so
        the next model request stays well-formed and the model sees a recoverable
        tool error instead of another provider 400.
        """
        normalized: list[dict] = []

        tool_calls = getattr(msg, "tool_calls", None) or []
        normalized.extend(list(tool_calls))

        raw_tool_calls = (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or []
        if not tool_calls:
            for raw_tc in raw_tool_calls:
                if not isinstance(raw_tc, dict):
                    continue

                function = raw_tc.get("function")
                name = raw_tc.get("name")
                if not name and isinstance(function, dict):
                    name = function.get("name")

                args = raw_tc.get("args", {})
                if not args and isinstance(function, dict):
                    raw_args = function.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            parsed_args = {}
                        args = parsed_args if isinstance(parsed_args, dict) else {}

                normalized.append(
                    {
                        "id": raw_tc.get("id"),
                        "name": name or "unknown",
                        "args": args if isinstance(args, dict) else {},
                    }
                )

        for invalid_tc in getattr(msg, "invalid_tool_calls", None) or []:
            if not isinstance(invalid_tc, dict):
                continue
            normalized.append(
                {
                    "id": invalid_tc.get("id"),
                    "name": invalid_tc.get("name") or "unknown",
                    "args": {},
                    "invalid": True,
                    "error": invalid_tc.get("error"),
                }
            )

        return normalized

    @staticmethod
    def _synthetic_tool_message_content(tool_call: dict) -> str:
        if tool_call.get("invalid"):
            name = tool_call.get("name")
            error = tool_call.get("error")
            error_text = error[:_MAX_RECOVERY_ERROR_DETAIL_LEN] if isinstance(error, str) and error else ""
            # Workaround for issue #2894: malformed write_file calls can carry huge Markdown
            # payloads in invalid tool-call args. Keep recovery guidance actionable without
            # echoing large or malformed content back to the model.
            if name == "write_file":
                details = f" Parser error: {error_text}" if error_text else ""
                return (
                    "[write_file failed before execution: the tool-call arguments were not valid JSON, "
                    "so no file was written. This often happens when the model tries to write a very "
                    "large Markdown file in a single tool call, especially when `content` contains "
                    "unescaped quotes, inline JSON, backslashes, or code fences. Do not retry the same "
                    "large `write_file` payload for this artifact; provide the report/content directly "
                    "as normal assistant text in your next response. If a file write is still needed "
                    f"later, split the file into smaller sections instead of one large payload.{details}]"
                )
            if error_text:
                return f"[Tool call could not be executed because its arguments were invalid: {error_text}]"
            return "[Tool call could not be executed because its arguments were invalid.]"
        return "[Tool call was interrupted and did not return a result.]"

    @staticmethod
    def _same_sequence(left: list, right: list) -> bool:
        """Return True when both lists hold the same message objects in the same order.

        Identity is the right test here: the normalisation reuses the very
        message objects it was given, so "same objects, same order" means the
        history is already well-formed and nothing has to be written back.
        """
        if len(left) != len(right):
            return False
        return all(a is b for a, b in zip(left, right))

    def _normalize_messages(self, messages: list) -> tuple[list, list[str]]:
        """Return the normalised history plus the ids of the calls that were repaired.

        The normalised list contains every input message exactly once, ordered the
        way the provider requires:

        * a ``ToolMessage`` that answers a declared tool call is placed
          immediately after the AIMessage that declared it,
        * a declared tool call with no result gets a synthetic error
          ``ToolMessage`` in that same position,
        * a ``ToolMessage`` with no matching call (an orphan) is left in place.

        The input list is never mutated.
        """
        tool_messages_by_id: dict[str, deque[ToolMessage]] = defaultdict(deque)
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_messages_by_id[msg.tool_call_id].append(msg)

        tool_call_ids: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if tc_id:
                    tool_call_ids.add(tc_id)

        patched: list = []
        dangling_ids: list[str] = []
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_call_ids:
                # Re-emitted next to the AIMessage that declared the call.
                continue

            patched.append(msg)
            if getattr(msg, "type", None) != "ai":
                continue

            for tc in self._message_tool_calls(msg):
                tc_id = tc.get("id")
                if not tc_id:
                    continue

                tool_msg_queue = tool_messages_by_id.get(tc_id)
                existing_tool_msg = tool_msg_queue.popleft() if tool_msg_queue else None
                if existing_tool_msg is not None:
                    patched.append(existing_tool_msg)
                else:
                    patched.append(
                        ToolMessage(
                            content=self._synthetic_tool_message_content(tc),
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    dangling_ids.append(tc_id)

        return patched, dangling_ids

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return messages with tool results grouped after their tool-call AIMessage.

        This normalizes model-bound causal order before provider serialization while
        preserving already-valid transcripts unchanged.
        """
        patched, _ = self._normalize_messages(messages)
        if self._same_sequence(patched, messages):
            return None
        return patched

    # ------------------------------------------------------------------
    # Run-scoped bookkeeping for the warning log
    # ------------------------------------------------------------------

    @staticmethod
    def _run_key(runtime) -> tuple[str, str]:
        """Return the ``(thread_id, run_id)`` key identifying the current run.

        The middleware instance is shared by every run the agent graph serves, so
        per-run state must never live directly on the instance.
        """
        context = getattr(runtime, "context", None)
        if not isinstance(context, dict):
            return ("default", "default")
        thread_id = context.get("thread_id")
        run_id = context.get("run_id")
        return (
            str(thread_id) if thread_id else "default",
            str(run_id) if run_id else "default",
        )

    def _evict_runs_locked(self) -> None:
        """Drop the oldest tracked runs once the limit is exceeded.

        Must be called while holding ``self._lock``. dict preserves insertion
        order, so the first key is the oldest tracked run.
        """
        while len(self._reported_ids) > self._MAX_TRACKED_RUNS:
            oldest = next(iter(self._reported_ids))
            self._reported_ids.pop(oldest, None)

    def _claim_unreported_ids(self, run_key: tuple[str, str], tool_call_ids: list[str]) -> list[str]:
        """Return the ids that have not been reported for this run yet."""
        if not tool_call_ids:
            return []
        with self._lock:
            reported = self._reported_ids.get(run_key)
            if reported is None:
                reported = {}
                self._reported_ids[run_key] = reported
                self._evict_runs_locked()
            new_ids = [tc_id for tc_id in tool_call_ids if tc_id not in reported]
            if new_ids:
                for tc_id in new_ids:
                    reported[tc_id] = None
                # Bound the per-run set: a pathological run must not grow it forever.
                while len(reported) > self._MAX_REPORTED_IDS_PER_RUN:
                    reported.pop(next(iter(reported)), None)
            return new_ids

    def _log_repair(self, run_key: tuple[str, str], tool_call_ids: list[str], *, persisted: bool) -> None:
        """Report a repair: WARNING for ids never seen in this run, DEBUG for repeats.

        The same dangling call used to be reported on every model call of a run,
        which buried the real signal in log spam. Reporting is now keyed by
        (thread_id, run_id) so each dangling tool-call id is warned about at
        most once per run.
        """
        if not tool_call_ids:
            return
        new_ids = self._claim_unreported_ids(run_key, tool_call_ids)
        if new_ids:
            ids = ", ".join(sorted(new_ids))
            if persisted:
                logger.warning("Resolved %d dangling tool call(s) in agent state: %s", len(new_ids), ids)
            else:
                logger.warning("Injecting %d placeholder ToolMessage(s) for dangling tool calls: %s", len(new_ids), ids)
        else:
            logger.debug(
                "Re-injected %d already-reported dangling tool call(s)%s",
                len(tool_call_ids),
                " in agent state" if persisted else "",
            )

    # ------------------------------------------------------------------
    # State-level repair (persisted once per run)
    # ------------------------------------------------------------------

    def _state_cleanup(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Resolve dangling tool calls in the persisted state, once per run.

        Returns the state update that replaces the message list with the
        normalised one, or ``None`` when the history is already well-formed (the
        common path — writing would cost a checkpoint on every model call).
        """
        messages = state.get("messages") if hasattr(state, "get") else None
        if not messages:
            return None

        patched, dangling_ids = self._normalize_messages(messages)
        if self._same_sequence(patched, messages):
            return None

        run_key = self._run_key(runtime)
        if dangling_ids:
            self._log_repair(run_key, dangling_ids, persisted=True)
        else:
            # Only a regrouping was needed (a tool result that sat later in the
            # history than the call it answers).
            logger.debug("DanglingToolCallMiddleware: regrouped tool results in agent state")

        # `add_messages` APPENDS messages it has not seen before, which would put
        # the synthetic ToolMessage at the end of the history and break the causal
        # order the provider requires. The REMOVE_ALL_MESSAGES sentinel makes the
        # reducer replace the list with ours verbatim, preserving the order.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *patched]}

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Persist the repair so later model calls of this run find a clean history."""
        try:
            return self._state_cleanup(state, runtime)
        except Exception:  # noqa: BLE001 — non-fatal middleware
            logger.debug("DanglingToolCallMiddleware: before_model cleanup failed", exc_info=True)
            return None

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Async version of before_model."""
        return self.before_model(state, runtime)

    # ------------------------------------------------------------------
    # Transient repair (safety net for the model-bound request)
    # ------------------------------------------------------------------

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        try:
            patched, dangling_ids = self._normalize_messages(request.messages)
            if not self._same_sequence(patched, request.messages):
                self._log_repair(self._run_key(getattr(request, "runtime", None)), dangling_ids, persisted=False)
                request = request.override(messages=patched)
        except Exception:  # noqa: BLE001 — non-fatal middleware
            logger.debug("DanglingToolCallMiddleware: wrap_model_call patch failed", exc_info=True)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        try:
            patched, dangling_ids = self._normalize_messages(request.messages)
            if not self._same_sequence(patched, request.messages):
                self._log_repair(self._run_key(getattr(request, "runtime", None)), dangling_ids, persisted=False)
                request = request.override(messages=patched)
        except Exception:  # noqa: BLE001 — non-fatal middleware
            logger.debug("DanglingToolCallMiddleware: awrap_model_call patch failed", exc_info=True)
        return await handler(request)
