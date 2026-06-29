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
from collections import deque
from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

# Per-(thread, deliverable-set) guard so the deterministic present_files self-test
# fires once per distinct set of presented artifacts, not on every tool cycle.
_verified_present: set[tuple[str, tuple[str, ...]]] = set()

# ── Generic build journal (project-agnostic) ──────────────────────────────────
# A deterministic, per-thread record of what THIS build did — derived purely from
# the agent's own tool activity (tool name + args), with zero project/framework
# assumptions. Written to ``workspace/BUILD_JOURNAL.md`` (named distinctly so it
# never collides with a project's own CHANGELOG.md; visible in the Files tab)
# and injected back as a ``<build_journal>`` reminder each turn so the agent never
# loses track of what it is building. In-memory tail keeps the injection off the
# event-loop hot path.
_BUILD_JOURNAL_MAX_LINES = 80
_BUILD_JOURNAL_INJECT_LINES = 40
_journals: dict[str, deque[str]] = {}
# Last journal tail injected per thread — used to skip re-injecting an unchanged
# block on consecutive model calls (idempotent injection).
_last_injected: dict[str, str] = {}

# Tools that mutate the build (worth journaling) vs. read-only/search tools (noise).
_JOURNAL_MUTATION_TOOLS = {
    "write_file",
    "str_replace",
    "bash",
    "start_dev_server",
    "stop_dev_server",
    "scaffold_project",
    "deploy_expose",
    "save_skill",
    "present_files",
    "dev_verify",
    "browser_check",
    "code_review",
}


def _journal_tool_args(messages: list, tool_call_id: str | None) -> dict:
    """Find the AIMessage tool_call args that produced *tool_call_id*.

    Args live on the dispatching AIMessage, not the ToolMessage, so we correlate
    by id. Returns an empty dict if not found. Never raises.
    """
    if not tool_call_id:
        return {}
    try:
        for m in reversed(messages):
            tool_calls = getattr(m, "tool_calls", None)
            if not tool_calls:
                continue
            for tc in tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id == tool_call_id:
                    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                    return args if isinstance(args, dict) else {}
    except Exception:
        logger.debug("build journal: arg correlation failed", exc_info=True)
    return {}


def _basename(path: Any) -> str:
    s = str(path or "").rstrip("/")
    return s.rsplit("/", 1)[-1] or s


def _derive_journal_line(messages: list) -> str | None:
    """Build one generic, timestamped journal line from the latest tool result.

    Generic by construction: the line is produced from the tool NAME + a couple of
    its argument/result fields only — no project, language, or domain knowledge.
    Returns None for read-only/uninteresting tools so the journal stays signal-rich.
    """
    try:
        tool_msg = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
        if tool_msg is None:
            return None
        name = (getattr(tool_msg, "name", None) or "").strip()
        if name not in _JOURNAL_MUTATION_TOOLS:
            return None

        content = getattr(tool_msg, "content", "")
        text = content if isinstance(content, str) else str(content)
        args = _journal_tool_args(messages, getattr(tool_msg, "tool_call_id", None))
        ts = datetime.now().strftime("%H:%M:%S")

        if name in ("write_file", "str_replace"):
            verb = "wrote" if name == "write_file" else "edited"
            path = args.get("path") or args.get("file_path")
            detail = _basename(path) if path else "a file"
            event = f"{verb} {detail}"
        elif name == "bash":
            cmd = str(args.get("command") or args.get("cmd") or "").strip().splitlines()
            event = f"$ {cmd[0][:80]}" if cmd else "ran a shell command"
        elif name in ("start_dev_server", "stop_dev_server"):
            event = name.replace("_", " ")
        elif name == "scaffold_project":
            event = f"scaffolded project ({args.get('template', 'template')})"
        elif name == "deploy_expose":
            event = "exposed a preview URL"
        elif name == "save_skill":
            event = "saved a skill"
        elif name == "present_files":
            event = "presented files to the user"
        elif name in ("dev_verify", "browser_check", "code_review"):
            verdict = "PASS" if "✅ PASS" in text else "ISSUES" if "⚠️ ISSUES" in text else ("ok" if '"ok": true' in text.lower() or "✓" in text else "checked")
            event = f"{name} → {verdict}"
        else:  # pragma: no cover — guarded by _JOURNAL_MUTATION_TOOLS
            event = name

        return f"- {ts}  {event}"
    except Exception:
        logger.debug("build journal: derive line failed", exc_info=True)
        return None


def _journal_header() -> str:
    return (
        "# Build Journal\n\n"
        "> Auto-maintained record of this build, derived from the agent's own tool\n"
        "> activity. The agent reads this each turn for continuity — it is the source\n"
        "> of truth for what has been built so far in this thread.\n\n"
        "## Activity\n"
    )


def _write_journal_file(thread_id: str, lines: list[str]) -> None:
    """Persist the journal to ``workspace/BUILD_JOURNAL.md`` (best-effort, generic).

    Named ``BUILD_JOURNAL.md`` (not ``CHANGELOG.md``) so it never collides with a
    project's own ``CHANGELOG.md``. Matches the agent-internal ``REVIEW.md`` /
    ``todo.md`` convention.
    """
    try:
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None

        work_dir = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "BUILD_JOURNAL.md").write_text(_journal_header() + "\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        logger.debug("build journal: file write skipped", exc_info=True)


def _record_journal_event(thread_id: str | None, messages: list) -> None:
    """Append a generic event line to the in-memory journal + BUILD_JOURNAL.md file."""
    if not thread_id:
        return
    line = _derive_journal_line(messages)
    if not line:
        return
    buf = _journals.setdefault(thread_id, deque(maxlen=_BUILD_JOURNAL_MAX_LINES))
    if buf and buf[-1] == line:  # dedup consecutive identical events
        return
    buf.append(line)
    _write_journal_file(thread_id, list(buf))


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

        check = await asyncio.to_thread(run_browser_check, thread_id, sandbox, routes=["/"], with_screenshot=True)
        verdict = "✓ passed" if check.ok else "✗ found issues"
        _append_devlog_to_sandbox_log(thread_id, f"[self-test] present_files browser check {verdict}")
        for r in check.routes:
            if not r.ok:
                _append_devlog_to_sandbox_log(thread_id, f"[self-test] {r.route} [{r.status}] {r.notes[:160]}")
            for ce in r.console_errors[:3]:
                _append_devlog_to_sandbox_log(thread_id, f"[self-test] console: {ce[:160]}")

        # Hand the rendered screenshot to a vision-capable model (ViewImageMiddleware
        # injects it next turn) so the agent can SEE the delivered build.
        from deerflow.agents.middlewares.verify_vision import _first_route_screenshot, stash_verify_screenshot

        shot_b64 = _first_route_screenshot(check)
        stash_verify_screenshot(thread_id, shot_b64)

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
                writer(
                    {
                        "type": "verify_result",
                        "thread_id": thread_id,
                        "ok": bool(check.ok),
                        "verdict": "passed" if check.ok else "issues",
                        "routes": routes_summary,
                        "console_errors_count": console_errors_count,
                        "screenshot": (f"data:image/png;base64,{shot_b64}" if shot_b64 else None),
                    }
                )
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

        thread_id = sandbox_id[len("local:") :]
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
            content = t.get("content") or t.get("description") if isinstance(t, dict) else getattr(t, "content", None) or getattr(t, "description", None) or str(t)
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

            # v7.4-d2: emit hook_emit via the run journal (non-fatal, wraps in try/except)
            self._try_record_hook(
                config,
                "observe_adjust",
                "aafter_tool",
                "observe",
                {
                    "todos_count": len(todos) if todos else 0,
                },
            )

            if todos:
                done = 0
                total = len(todos)
                for t in todos:
                    status = t.get("status") if isinstance(t, dict) else getattr(t, "status", None)
                    if status in ("completed", "done"):
                        done += 1

                task_status = "completed" if done >= total else "in_progress"

                # Emit task_progress custom event for the frontend
                if callable(writer):
                    writer(
                        {
                            "type": "task_progress",
                            "step": done,
                            "total": total,
                            "status": task_status,
                        }
                    )

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
                            writer(
                                {
                                    "type": "task_activity",
                                    "tool_call_id": getattr(msg, "tool_call_id", ""),
                                    "status": "done",
                                }
                            )
                        break  # only emit for the very latest tool message

            # Deterministic auto-verify on present_files: when the latest tool result
            # is a successful present_files, self-test the delivered build in the
            # background (no model choice). Mirrors auto-verify-on-preview.
            self._maybe_verify_present_files(state, config, messages, writer)

            # Generic build journal: record what this tool cycle did (project-agnostic).
            thread_id = ((config.get("configurable") or {}).get("thread_id")) if isinstance(config, dict) else None
            _record_journal_event(thread_id, messages)

        except Exception:
            logger.warning("ObserveAdjustMiddleware: error in aafter_tool", exc_info=True)

        return state

    def _maybe_verify_present_files(self, state: Any, config: RunnableConfig, messages: list, writer: Any = None) -> None:
        """Fire a one-shot background browser_check when present_files just succeeded.

        ``writer`` is the LangGraph custom-event stream writer (from
        ``config["writer"]``); threaded in so the background verify can emit the
        ``verify_result`` event. It was previously referenced but never passed,
        raising a NameError that the broad ``except`` below swallowed — so the
        gate silently never fired.
        """
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

    # ── Build-journal injection ───────────────────────────────────────────────
    # Append the current build journal as a trailing <build_journal> reminder so
    # the model always has continuity of what it is building. Transient (via
    # request.override) — never mutates persisted state. Only injects when a
    # journal exists (active build), so plain Q&A chats are untouched. Reads from
    # the in-memory tail (no event-loop file IO). Non-fatal by construction.
    def _inject_journal(self, request: ModelRequest) -> ModelRequest:
        try:
            runtime = getattr(request, "runtime", None)
            ctx = getattr(runtime, "context", None) if runtime is not None else None
            thread_id: Any = None
            if ctx is not None:
                try:
                    thread_id = ctx.get("thread_id")
                except Exception:
                    thread_id = getattr(ctx, "thread_id", None)
            tid = str(thread_id) if thread_id else None
            buf = _journals.get(tid) if tid else None
            if not buf:
                return request
            tail = list(buf)[-_BUILD_JOURNAL_INJECT_LINES:]
            # Idempotent: only inject when the journal changed since the last
            # injection for this thread. Avoids re-appending the same block every
            # turn (token cost + prompt-cache churn). Durable continuity lives in
            # workspace/BUILD_JOURNAL.md (the prompt tells the agent to read it).
            tail_key = "\n".join(tail)
            if tid and _last_injected.get(tid) == tail_key:
                return request
            if tid:
                _last_injected[tid] = tail_key
            reminder = (
                "<system-reminder>\n<build_journal>\n"
                "Auto-maintained record of what you have built so far in this thread "
                "(source of truth — mirrored in workspace/BUILD_JOURNAL.md). Use it to stay "
                "consistent with prior steps; do not repeat completed work.\n" + "\n".join(tail) + "\n</build_journal>\n</system-reminder>"
            )
            new_messages = [*request.messages, HumanMessage(content=reminder, name="build_journal")]
            return request.override(messages=new_messages)
        except Exception:
            logger.debug("ObserveAdjustMiddleware: build journal inject skipped", exc_info=True)
            return request

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelCallResult:
        return handler(self._inject_journal(request))

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelCallResult:
        return await handler(self._inject_journal(request))

    @staticmethod
    def _try_record_hook(config: Any, tag: str, hook: str, action: str, changes: dict) -> None:
        """Write a middleware audit event to the run journal (non-fatal, best-effort).

        Works from tool-lifecycle hooks (aafter_tool/after_tool) where the journal
        lives at config["configurable"]["__pregel_runtime"].context["__run_journal"].
        Silently skips when the journal is unavailable (unit tests, subagents,
        no-event-store paths).
        """
        try:
            runtime = (config.get("configurable") or {}).get("__pregel_runtime") if isinstance(config, dict) else None
            ctx = getattr(runtime, "context", None) if runtime is not None else None
            journal = ctx.get("__run_journal") if isinstance(ctx, dict) else None
            if journal is None:
                return
            journal.record_middleware(
                tag,
                name="ObserveAdjustMiddleware",
                hook=hook,
                action=action,
                changes=changes,
            )
        except Exception:
            pass
