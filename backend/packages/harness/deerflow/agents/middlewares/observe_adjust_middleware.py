"""Observe-and-adjust middleware.

After every tool cycle:
1. Reads the current todo list from state and emits a ``task_progress``
   custom event so the frontend's Agent's Computer panel shows a live step counter.
2. Writes ``/mnt/user-data/workspace/todo.md`` to the sandbox filesystem so the
   ``/api/sandbox/todo`` endpoint can serve it directly.

Both operations are wrapped in try/except so failures never affect the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

# Per-(thread, deliverable-set) guard so the deterministic present_files self-test
# fires once per distinct set of presented artifacts, not on every tool cycle.
_verified_present: set[tuple[str, tuple[str, ...]]] = set()


def _is_local_sandbox_id(sandbox_id: str | None) -> bool:
    return bool(sandbox_id) and (sandbox_id == "local" or sandbox_id.startswith("local:"))


async def _auto_verify_present_files(thread_id: str, sandbox_id: str, writer=None) -> None:
    """Deterministic self-test of a just-presented deliverable.

    Runs ``browser_check`` against the latest present_files HTML (auto-detected by
    run_browser_check when no dev server is up) so EVERY presented build is verified
    with zero model choice. Mirrors auto-verify-on-preview. Fully best-effort: any
    failure is swallowed and never affects the run.

    When *writer* is provided, also emits a ``verify_result`` custom event so the
    frontend Activity tab can render a compact pill. The event payload is shaped
    for forward-compatibility (extensible fields, JSON-serialisable). Failures
    to emit are swallowed — the verify_result event is a UX nicety, never a
    correctness gate.
    """
    try:
        from deerflow.sandbox.browser_check import run_browser_check
        from deerflow.sandbox.dev_server import _append_devlog_to_sandbox_log
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        sandbox = get_sandbox_provider().get(sandbox_id)
        if sandbox is None or getattr(sandbox, "_client", None) is None:
            return  # no browser-capable (AIO) sandbox → nothing to verify

        check = await asyncio.to_thread(
            run_browser_check, thread_id, sandbox, routes=["/"], with_screenshot=True
        )
        verdict = "✓ passed" if check.ok else "✗ found issues"
        _append_devlog_to_sandbox_log(thread_id, f"[self-test] present_files browser check {verdict}")
        for r in check.routes:
            if not r.ok:
                _append_devlog_to_sandbox_log(thread_id, f"[self-test] {r.route} [{r.status}] {r.notes[:160]}")
            for ce in r.console_errors[:3]:
                _append_devlog_to_sandbox_log(thread_id, f"[self-test] console: {ce[:160]}")

        # Surface a structured event for the frontend (Activity tab pill).
        # Non-fatal: any failure here is logged and swallowed so it can
        # never affect the run.
        if callable(writer):
            try:
                # Build a compact, JSON-serialisable summary.
                routes_summary = [
                    {
                        "route": r.route,
                        "ok": bool(r.ok),
                        "status": getattr(r, "status", None),
                        "notes": (r.notes or "")[:200],
                    }
                    for r in check.routes
                ]
                console_errors_count = sum(len(r.console_errors or []) for r in check.routes)
                writer({
                    "type": "verify_result",
                    "thread_id": thread_id,
                    "ok": bool(check.ok),
                    "verdict": "passed" if check.ok else "issues",
                    "routes": routes_summary,
                    "console_errors_count": console_errors_count,
                    "screenshot": getattr(check, "screenshot_b64", None),  # may be None
                })
            except Exception:
                logger.debug("verify_result event emit failed", exc_info=True)
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("auto-verify present_files failed for %s: %s", thread_id, e)


def _write_todo_md_file(todos: list, state: Any) -> None:
    """Write todo.md to the sandbox workspace directory.

    Only operates when a sandbox_id is present in state. Silently skips
    when sandbox is unavailable or not a local per-thread sandbox.
    """
    try:
        sandbox_state = (state.get("sandbox") if hasattr(state, "get") else None) or {}
        sandbox_id = sandbox_state.get("sandbox_id") if isinstance(sandbox_state, dict) else None
        if not sandbox_id or not sandbox_id.startswith("local:"):
            return

        thread_id = sandbox_id[len("local:"):]
        if not thread_id:
            return

        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None

        paths = get_paths()
        work_dir = paths.sandbox_work_dir(thread_id, user_id=user_id)
        work_dir.mkdir(parents=True, exist_ok=True)

        lines = ["# Task Progress\n"]
        for t in todos:
            status = t.get("status") if isinstance(t, dict) else getattr(t, "status", "pending")
            content = (t.get("content") or t.get("description") if isinstance(t, dict)
                       else getattr(t, "content", None) or getattr(t, "description", None) or str(t))
            if status == "completed":
                cb = "[x]"
            elif status == "in_progress":
                cb = "[~]"
            else:
                cb = "[ ]"
            lines.append(f"- {cb} {content or ''}")

        todo_path = work_dir / "todo.md"
        todo_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        logger.debug("ObserveAdjustMiddleware: error writing todo.md", exc_info=True)


class ObserveAdjustMiddleware(AgentMiddleware):
    """Emit ``task_progress`` custom events and write todo.md after each tool cycle."""

    async def aafter_tool(self, state: Any, config: RunnableConfig) -> Any:
        try:
            todos = (state.get("todos") if hasattr(state, "get") else None) or []
            writer = config.get("writer") if isinstance(config, dict) else None

            if todos:
                done = 0
                total = len(todos)
                for t in todos:
                    status = (
                        t.get("status") if isinstance(t, dict)
                        else getattr(t, "status", None)
                    )
                    if status in ("completed", "done"):
                        done += 1

                task_status = "completed" if done >= total else "in_progress"

                # Emit task_progress custom event for the frontend
                if callable(writer):
                    writer({
                        "type": "task_progress",
                        "step": done,
                        "total": total,
                        "status": task_status,
                    })

                # Write todo.md to the sandbox workspace (best-effort)
                _write_todo_md_file(todos, state)

            # Also check the latest messages for completed task tool calls and
            # emit a lightweight task_activity event so the frontend can derive
            # todo completion from subagent task completions even when
            # thread.values.todos hasn't updated yet.
            messages = (state.get("messages") if hasattr(state, "get") else None) or []
            if messages and callable(writer):
                for msg in reversed(messages[-10:]):  # only check recent messages
                    if isinstance(msg, ToolMessage):
                        tool_name = getattr(msg, "name", None) or ""
                        if tool_name == "task":
                            writer({
                                "type": "task_activity",
                                "tool_call_id": getattr(msg, "tool_call_id", ""),
                                "status": "done",
                            })
                        break  # only emit for the very latest tool message

            # Deterministic auto-verify on present_files: when the latest tool result
            # is a successful present_files, self-test the delivered build in the
            # background (no model choice). Mirrors auto-verify-on-preview.
            self._maybe_verify_present_files(state, config, messages)

        except Exception:
            logger.warning("ObserveAdjustMiddleware: error in aafter_tool", exc_info=True)

        return state

    def _maybe_verify_present_files(self, state: Any, config: RunnableConfig, messages: list) -> None:
        """Fire a one-shot background browser_check when present_files just succeeded."""
        try:
            latest_tool = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
            if latest_tool is None or (getattr(latest_tool, "name", None) or "") != "present_files":
                return

            sandbox_state = (state.get("sandbox") if hasattr(state, "get") else None) or {}
            sandbox_id = sandbox_state.get("sandbox_id") if isinstance(sandbox_state, dict) else None
            if not sandbox_id or _is_local_sandbox_id(sandbox_id):
                return  # browser_check needs the AIO chromium; local sandbox has none

            thread_id = ((config.get("configurable") or {}).get("thread_id")) if isinstance(config, dict) else None
            if not thread_id:
                return

            artifacts = tuple(sorted((state.get("artifacts") if hasattr(state, "get") else None) or []))
            key = (thread_id, artifacts)
            if key in _verified_present:
                return
            _verified_present.add(key)

            # Results surface via the [self-test] lines appended to sandbox.log,
            # which the Terminal tab already tails (a proven channel).
            # Additionally, we now emit a structured verify_result custom event so
            # the frontend Activity tab can render a compact pill. The event is
            # additive: the Terminal-tab proven channel stays the source of truth.
            asyncio.create_task(_auto_verify_present_files(thread_id, sandbox_id, writer=writer if callable(writer) else None))
        except Exception:
            logger.debug("ObserveAdjustMiddleware: present_files auto-verify skipped", exc_info=True)

    def after_tool(self, state: Any, config: RunnableConfig) -> Any:
        # Sync path: writer not available; still write todo.md
        try:
            todos = (state.get("todos") if hasattr(state, "get") else None) or []
            if todos:
                _write_todo_md_file(todos, state)
        except Exception:
            logger.debug("ObserveAdjustMiddleware: error in after_tool", exc_info=True)
        return state
