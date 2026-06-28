"""Reflect/fix budget middleware — runtime-enforces the prompt's "iterate at most twice" rule.

The prompt's ``<self_verify>`` block tells the agent: call ``dev_verify``,
fix any ISSUES, re-verify, iterate at most twice, then write a final
answer. But the prompt is just text. Without runtime enforcement, the
agent can ignore the rule and keep iterating until the recursion limit
kills the run (a known v3 failure mode).

This middleware closes that gap. It watches ``dev_verify`` ToolMessages,
counts consecutive ISSUES results per (thread_id, run_id), and injects a
forced HumanMessage after the budget is hit telling the agent to stop
and write its final answer.

State machine (per thread_id, run_id):

    dev_verify result          counter     action
    ─────────────────          ───────     ──────
    PASS                       0           reset (already 0)
    error / non-dev_verify     unchanged   ignore (tooling failure ≠ agent failure)
    ISSUES (1st)               1           continue (agent should iterate)
    ISSUES (2nd)               2 = budget  inject forced HumanMessage
    ISSUES (3rd+)              2 (capped)  re-inject forced message

Budget is reset at run start (``before_agent``) so a previous run's
failures don't carry over into a new run on the same thread.

Non-fatal by construction: every code path is wrapped in try/except so
a failure here can never break the run. The middleware degrades to
inert (no detection, no warnings) if anything goes wrong.
"""

from __future__ import annotations

import logging
import threading
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_DEFAULT_REFLECT_BUDGET = 2


class ReflectFixBudgetMiddleware(AgentMiddleware[AgentState]):
    """Runtime-enforce the prompt's "iterate at most twice" rule.

    Counts consecutive ``dev_verify`` ISSUES results per (thread, run).
    When the budget is hit, queues a forced HumanMessage that the next
    ``wrap_model_call`` injects at the end of the message list — same
    pattern as ``LoopDetectionMiddleware`` so the existing tool-call
    pairing invariants are preserved.

    Args:
        budget: Max consecutive ISSUES before forcing a final answer.
            Default 2 (matches the prompt's "iterate at most twice").
    """

    def __init__(self, budget: int = _DEFAULT_REFLECT_BUDGET) -> None:
        super().__init__()
        if budget < 1:
            raise ValueError(f"budget must be >= 1, got {budget}")
        self.budget = budget
        self._lock = threading.Lock()
        self._consecutive_issues: dict[tuple[str, str], int] = {}
        self._pending_warnings: dict[tuple[str, str], list[str]] = {}

    @staticmethod
    def _keys(runtime: Runtime) -> tuple[str, str]:
        thread_id = str(runtime.context.get("thread_id", "default")) if runtime.context else "default"
        run_id = str(runtime.context.get("run_id", "default")) if runtime.context else "default"
        return thread_id, run_id

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        keys = self._keys(runtime)
        with self._lock:
            self._consecutive_issues.pop(keys, None)
            self._pending_warnings.pop(keys, None)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.before_agent(state, runtime)

    def _classify_dev_verify(self, content: str) -> str | None:
        """Return 'pass', 'issues', 'error', or None.

        - 'pass': verdict line says PASS → reset budget.
        - 'issues': verdict line says ISSUES → increment budget.
        - 'error': result is an Error: ... string → ignore (tooling failure).
        - None: not a recognised dev_verify shape → ignore.
        """
        if not isinstance(content, str):
            return None
        # Error path — dev_verify tool returned an Error: ... string.
        # This is a tooling failure, not an agent-failed-verification; ignore.
        stripped = content.lstrip()
        if stripped.startswith("Error:"):
            return "error"
        if "✅ PASS" in content:
            return "pass"
        if "⚠️ ISSUES" in content:
            return "issues"
        return None

    @staticmethod
    def _find_last_tool_message(messages: list) -> object | None:
        """Return the last ToolMessage in *messages*, or None.

        Walks the list in reverse so the cost is bounded by the position
        of the most recent ToolMessage (typically the very last or near
        the last message in a tool-call turn).
        """
        for m in reversed(messages):
            if getattr(m, "type", None) == "tool":
                return m
        return None

    @staticmethod
    def _parse_dev_verify_issues(content: str) -> str:
        """Extract the concrete failing lines from a dev_verify markdown report.

        Generic by construction — keys off failure MARKERS only (✗, 🔴, error,
        fail, render/console/unreachable/blank route statuses), with zero project
        or framework knowledge. Bounded so the injected directive stays small.
        """
        if not isinstance(content, str):
            return ""
        markers = (
            "✗", "🔴", "error", "fail", "[render_error]", "[console_errors]",
            "[unreachable]", "[blank]",
        )
        out: list[str] = []
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("# "):
                continue
            low = line.lower()
            if any(m in low for m in markers):
                out.append(line if line.startswith(("-", "*")) else f"- {line}")
            if len(out) >= 15:
                break
        return "\n".join(out)

    def _on_issues(self, runtime: Runtime, content: str = "") -> tuple[str | None, int]:
        """Increment budget; queue a forced HumanMessage for the next model call.

        Drive-to-green (bounded):
        - **Under budget**: queue a DRIVE directive quoting the concrete failing
          items so the agent fixes exactly those and re-verifies. (Previously this
          was silent — the agent was merely *asked* to iterate by the prompt.)
        - **At/over budget**: queue a STOP directive forcing an honest final answer.

        Always returns the queued directive + the new count.
        """
        keys = self._keys(runtime)
        with self._lock:
            count = self._consecutive_issues.get(keys, 0) + 1
            self._consecutive_issues[keys] = count
            if count >= self.budget:
                warning = (
                    f"[REFLECT BUDGET HIT] dev_verify has returned ISSUES "
                    f"{count} times consecutively. Per the <self_verify> rule, "
                    f"you MUST stop iterating and write your final answer now. "
                    f"Tell the user plainly what is wrong and what you have "
                    f"accomplished. Do not call any more tools."
                )
                self._pending_warnings.setdefault(keys, []).append(warning)
                return warning, count
            directive = (
                f"[VERIFY FAILED — FIX REQUIRED] dev_verify returned ISSUES "
                f"(attempt {count} of {self.budget}). Fix exactly the items below, then "
                f"call dev_verify again. Do NOT present final results or tell the user "
                f"you are done until dev_verify returns PASS."
            )
            issues = self._parse_dev_verify_issues(content)
            if issues:
                directive += "\nFailing items:\n" + issues
            self._pending_warnings.setdefault(keys, []).append(directive)
            return directive, count

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        try:
            messages = state.get("messages", []) if hasattr(state, "get") else []
            if not messages:
                return None

            last_tool = self._find_last_tool_message(messages)
            if last_tool is None:
                return None

            if getattr(last_tool, "name", None) != "dev_verify":
                return None

            verdict = self._classify_dev_verify(getattr(last_tool, "content", ""))
            if verdict == "pass":
                with self._lock:
                    self._consecutive_issues.pop(self._keys(runtime), None)
                return None
            if verdict == "issues":
                self._on_issues(runtime, getattr(last_tool, "content", "") or "")
            # verdict == "error" or None → ignore
        except Exception:  # noqa: BLE001 — middleware is non-fatal
            logger.debug("ReflectFixBudgetMiddleware: after_model failed", exc_info=True)
        return None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_model(state, runtime)

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        keys = self._keys(runtime)
        with self._lock:
            warnings = self._pending_warnings.pop(keys, [])
        return warnings

    def _inject_into_request(self, request: ModelRequest) -> ModelRequest:
        warnings = self._drain_pending_warnings(request.runtime)
        if not warnings:
            return request
        deduped = list(dict.fromkeys(warnings))
        new_messages = [
            *request.messages,
            HumanMessage(content="\n\n".join(deduped), name="reflect_budget"),
        ]
        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ) -> ModelCallResult:
        return handler(self._inject_into_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ) -> ModelCallResult:
        return await handler(self._inject_into_request(request))

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                keys_to_drop = [k for k in self._consecutive_issues if k[0] == thread_id]
                for k in keys_to_drop:
                    self._consecutive_issues.pop(k, None)
                    self._pending_warnings.pop(k, None)
            else:
                self._consecutive_issues.clear()
                self._pending_warnings.clear()


__all__ = ["ReflectFixBudgetMiddleware"]