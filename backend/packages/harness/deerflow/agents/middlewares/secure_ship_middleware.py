"""SecureShipMiddleware - the shipping gate in front of every delivery tool.

Nova ships an app by calling ``deploy_expose`` (live preview URL) or
``present_files`` (the deliverable itself). Those two calls are the moment a
build becomes visible to somebody else, so they are where the Secure-Ship gate
belongs: the gate scans the thread's build and, when it finds a release
blocker, the delivery never happens and the agent is told exactly what to fix.

Two rules shape the implementation:

* **The gate's verdict is the only thing that blocks.** A ``block`` verdict
  means the handler is not called at all -- the deploy never ships.
* **An unavailable gate never blocks.** If the scanner cannot run (no sandbox,
  engine missing, a crash), the delivery proceeds with a note saying so. A
  broken scanner must not brick Nova, and the gate's own verdict already
  covers the case that actually matters.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# The engine is imported defensively so importing this middleware can never
# fail: an absent engine means the gate cannot run, and an absent gate means
# the delivery is not blocked (fail open, with a note).
try:  # pragma: no cover - exercised through the middleware's own paths
    from deerflow.security.secure_ship import run_gate
except Exception as _engine_import_error:  # noqa: BLE001 - any import failure is "engine missing"
    run_gate = None  # type: ignore[assignment]
    logger.debug("Secure-Ship gate engine unavailable: %s", _engine_import_error)


#: The tools that hand a build to the user. These are the delivery moment.
DELIVERY_TOOLS: frozenset[str] = frozenset({"deploy_expose", "present_files"})

#: Severities, worst first -- the order a summary reads in.
_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")

#: How many findings a block message names before it stops.
_MAX_LISTED_FINDINGS = 3


def _get(obj: Any, names: tuple[str, ...]) -> Any:
    """First attribute (or dict key) of *obj* named in *names*."""
    if obj is None:
        return None
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
            continue
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _severity(finding: Any) -> str:
    """A finding's severity, lower-cased (``"unknown"`` when it has none)."""
    raw = _get(finding, ("severity", "level", "risk"))
    if raw is None:
        return "unknown"
    return str(raw).strip().lower()


def _finding_title(finding: Any) -> str:
    """A finding's one-line description."""
    raw = _get(finding, ("title", "message", "summary", "description", "name"))
    return str(raw).strip() if raw else ""


def _finding_path(finding: Any) -> str:
    """The file a finding points at (relative, when the engine says so)."""
    raw = _get(finding, ("path", "file", "filepath", "location", "target"))
    return str(raw).strip() if raw else ""


def _finding_label(finding: Any) -> str:
    """``Possible hard-coded secret in app/config.py`` -- title plus file."""
    title = _finding_title(finding)
    path = _finding_path(finding)
    if title and path and path not in title:
        return f"{title} in {path}"
    return title or path


#: Keys a counts mapping may use for "how many findings in total" -- they are
#: a total, not a severity, and are kept out of the severity tally.
_TOTAL_KEYS: frozenset[str] = frozenset({"total", "total_findings", "findings_total", "count"})


def _raw_counts(report: Any) -> Any:
    """The report's counts object, whatever shape the engine gave it."""
    return _get(report, ("counts", "count", "severity_counts"))


def _counts(report: Any) -> dict[str, int]:
    """The report's severity counts, as ``{severity: count}``.

    The engine's own ``counts`` is preferred; a report that only carries
    findings is tallied from them. A ``total`` key is not a severity and is
    left out of the tally.
    """
    raw = _raw_counts(report)
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        # A counts mapping of {severity: count}; a nested {severity: {count}}
        # is flattened one level.
        if raw and all(isinstance(v, dict) for v in raw.values()):
            for inner in raw.values():
                for key, value in inner.items():
                    if isinstance(value, (int, float)) and str(key).lower() not in _TOTAL_KEYS:
                        out[str(key).lower()] = out.get(str(key).lower(), 0) + int(value)
        else:
            for key, value in raw.items():
                if isinstance(value, (int, float)) and str(key).lower() not in _TOTAL_KEYS:
                    out[str(key).lower()] = int(value)
        return out
    if raw is not None:
        for severity in _SEVERITIES:
            value = getattr(raw, severity, None)
            if isinstance(value, (int, float)):
                out[severity] = int(value)
        if out:
            return out
    for finding in _findings(report):
        severity = _severity(finding)
        out[severity] = out.get(severity, 0) + 1
    return out


def _total(report: Any) -> int:
    """How many findings the report carries in total."""
    raw = _raw_counts(report)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if str(key).lower() in _TOTAL_KEYS and isinstance(value, (int, float)):
                return int(value)
    counts = _counts(report)
    if counts:
        return sum(counts.values())
    return len(_findings(report))


def _findings(report: Any) -> list[Any]:
    """The report's findings, as a list (empty when it carries none)."""
    raw = _get(report, ("findings", "issues"))
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _ordered_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    """The non-zero counts, worst severity first."""
    known = [(s, counts[s]) for s in _SEVERITIES if counts.get(s)]
    extra = [(s, n) for s, n in sorted(counts.items()) if n and s not in _SEVERITIES]
    return known + extra


def _headline(report: Any) -> tuple[str, int]:
    """``("1 high finding", 1)`` -- what was found, in one clause.

    The first element is the human label; the second is the total number of
    findings.
    """
    counts = _counts(report)
    total = _total(report)
    ordered = _ordered_counts(counts)
    parts = [f"{n} {severity}" for severity, n in ordered]
    if not parts:
        return f"{total} finding{'' if total == 1 else 's'}", total
    headline = " and ".join(parts)
    mentioned = sum(n for _, n in ordered)
    plural = "finding" if mentioned == 1 else "findings"
    return f"{headline} {plural}", total


def _details(report: Any) -> str:
    """The worst findings, named: ``Possible hard-coded secret in app/config.py``."""
    counts = _counts(report)
    findings = _findings(report)
    ordered = _ordered_counts(counts)
    top: list[Any] = []
    if ordered:
        worst_severity = ordered[0][0]
        top = [f for f in findings if _severity(f) == worst_severity]
    if not top:
        top = findings[:_MAX_LISTED_FINDINGS]
    else:
        top = top[:_MAX_LISTED_FINDINGS]
    return "; ".join(text for text in (_finding_label(f) for f in top) if text)


def _summarize(report: Any) -> tuple[str, int]:
    """``("1 high finding (Possible hard-coded secret in app/config.py)", 1)``.

    The first element is the human summary of what was found; the second is
    the total number of findings.
    """
    label, total = _headline(report)
    details = _details(report)
    return (f"{label} ({details})" if details else label), total


def _resolve_run_gate() -> Any:
    """The gate entry point: the module global (patchable), else the engine's."""
    if run_gate is not None:
        return run_gate
    try:  # pragma: no cover - only when the engine landed after import
        from deerflow.security.secure_ship import run_gate as engine_run_gate

        return engine_run_gate
    except Exception:  # noqa: BLE001
        return None


class SecureShipMiddleware(AgentMiddleware[ThreadState]):
    """Gate every delivery (``deploy_expose``, ``present_files``) on Secure-Ship.

    On a delivery call the middleware runs the gate for the current thread:

    * ``block`` -- the handler is **not** called. The agent gets an error
      ``ToolMessage`` naming what was found and what to fix.
    * ``warn`` / ``pass`` -- the handler runs and the result carries a short
      advisory note.
    * the gate could not run -- the handler runs anyway (fail open) and the
      result carries a note saying the gate was unavailable.
    """

    state_schema = ThreadState

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_thread_id(self, request: ToolCallRequest) -> str | None:
        """The thread the delivery belongs to (context first, then config)."""
        runtime = request.runtime  # ToolRuntime; may be None-like in tests
        if runtime is None:
            return None
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, dict) else None
        if thread_id is None:
            cfg = getattr(runtime, "config", None) or {}
            configurable = cfg.get("configurable", {}) if isinstance(cfg, dict) else {}
            if isinstance(configurable, dict):
                thread_id = configurable.get("thread_id")
        return thread_id

    def _get_user_id(self, request: ToolCallRequest) -> str | None:
        """The user id the runtime carries, when it carries one."""
        runtime = request.runtime
        ctx = getattr(runtime, "context", None) or {}
        if isinstance(ctx, dict):
            user_id = ctx.get("user_id")
            if user_id:
                return str(user_id)
        return None

    async def _run_gate_async(self, thread_id: str, user_id: str | None) -> Any:
        """Await the gate for *thread_id*."""
        gate = _resolve_run_gate()
        if gate is None:
            raise RuntimeError("the Secure-Ship gate engine is not available")
        return await gate(thread_id, user_id=user_id, deep=False)

    def _run_gate_sync(self, thread_id: str, user_id: str | None) -> Any:
        """Run the async gate from a synchronous tool path.

        ``asyncio.run`` covers the normal case (LangGraph calls the sync
        ``wrap_tool_call`` from a worker thread with no running loop). When a
        loop *is* running in this thread, the coroutine is driven on a private
        loop in a worker thread instead of deadlocking.
        """
        try:
            return asyncio.run(self._run_gate_async(thread_id, user_id))
        except RuntimeError as exc:
            if "running event loop" not in str(exc):
                raise
            outcome: dict[str, Any] = {}

            def _worker() -> None:
                try:
                    outcome["report"] = asyncio.run(self._run_gate_async(thread_id, user_id))
                except BaseException as inner:  # noqa: BLE001 - reported to the caller
                    outcome["error"] = inner

            thread = threading.Thread(target=_worker, name="secure-ship-gate", daemon=True)
            thread.start()
            thread.join()
            if "error" in outcome:
                raise outcome["error"]
            return outcome.get("report")

    def _append_note(self, result: ToolMessage | Command, note: str) -> ToolMessage | Command:
        """Append an advisory note to a tool result (non-blocking)."""
        if not isinstance(result, ToolMessage):
            return result
        if isinstance(result.content, list):
            new_content = list(result.content) + [{"type": "text", "text": note}]
        else:
            new_content = str(result.content) + note
        return ToolMessage(
            content=new_content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            status=result.status,
        )

    def _build_block_message(self, request: ToolCallRequest, summary: str) -> ToolMessage:
        """The error the agent sees instead of a shipped build."""
        tool_call_id = str(request.tool_call.get("id") or "missing_id")
        name = str(request.tool_call.get("name") or "unknown_tool")
        return ToolMessage(
            content=f"Secure-Ship blocked this deploy: {summary}. Fix it and retry.",
            tool_call_id=tool_call_id,
            name=name,
            status="error",
        )

    def _advisory_note(self, report: Any) -> str:
        """The note appended to a delivery the gate let through."""
        label, total = _headline(report)
        verdict = str(_get(report, ("verdict",)) or "pass").strip().lower()
        if verdict == "warn":
            return f"Secure-Ship: {label}, review before shipping."
        plural = "" if total == 1 else "s"
        return f"Secure-Ship: gate passed ({total} finding{plural})."

    def _emit_event(self, thread_id: str | None, report: Any) -> None:
        """Tell the UI what the gate decided (best effort, never a failure)."""
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 - no stream context: nothing to do
            logger.debug("get_stream_writer unavailable; skipping secure_ship event", exc_info=True)
            return
        if not callable(writer):
            return
        try:
            writer(
                {
                    "type": "secure_ship",
                    "thread_id": thread_id,
                    "verdict": _get(report, ("verdict",)),
                    "counts": _counts(report),
                    "duration_ms": _get(report, ("duration_ms", "elapsed_ms")),
                    "reason": _get(report, ("reason",)),
                }
            )
        except Exception:  # noqa: BLE001 - a UX nicety must never fail a run
            logger.debug("Failed to emit secure_ship stream event", exc_info=True)

    # ------------------------------------------------------------------
    # Core logic (shared between sync and async paths)
    # ------------------------------------------------------------------

    def _is_delivery(self, request: ToolCallRequest) -> bool:
        """Whether this tool call ships a build to the user."""
        name = request.tool_call.get("name")
        return isinstance(name, str) and name in DELIVERY_TOOLS

    def _outcome(self, report: Any) -> tuple[str, str]:
        """What the gate's verdict means for this delivery.

        ``("block", summary)`` stops the delivery; ``("note", text)`` lets it
        through with an advisory note attached to the result.
        """
        verdict = str(_get(report, ("verdict",)) or "pass").strip().lower()
        if verdict == "block":
            summary, _ = _summarize(report)
            return "block", summary
        if verdict == "error":
            # The gate ran but could not decide: that is a failure to scan,
            # not a clean bill of health. Fail open, and say so.
            reason = _get(report, ("reason",)) or "the gate reported an error"
            return "note", self._gate_failure_note(str(reason))
        return "note", self._advisory_note(report)

    def _gate_failure_note(self, reason: str) -> str:
        """The note appended when the gate could not run (fail open)."""
        return f"Secure-Ship could not run: {reason}"

    # ------------------------------------------------------------------
    # wrap_tool_call hooks
    # ------------------------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if not self._is_delivery(request):
            return handler(request)

        thread_id = self._get_thread_id(request)
        if not thread_id:
            logger.warning("[SecureShip] no thread in scope for %s; shipping without a gate", request.tool_call.get("name"))
            return self._append_note(handler(request), "\n\n" + self._gate_failure_note("no thread in scope"))

        try:
            report = self._run_gate_sync(thread_id, self._get_user_id(request))
        except Exception as exc:  # noqa: BLE001 - fail open, never brick Nova
            logger.warning("[SecureShip] gate failed for thread=%s: %s", thread_id, exc, exc_info=True)
            return self._append_note(handler(request), "\n\n" + self._gate_failure_note(str(exc) or exc.__class__.__name__))

        self._emit_event(thread_id, report)
        outcome, text = self._outcome(report)
        if outcome == "block":
            logger.warning("[SecureShip] BLOCKED thread=%s tool=%s %s", thread_id, request.tool_call.get("name"), text)
            return self._build_block_message(request, text)
        return self._append_note(handler(request), "\n\n" + text)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if not self._is_delivery(request):
            return await handler(request)

        thread_id = self._get_thread_id(request)
        if not thread_id:
            logger.warning("[SecureShip] no thread in scope for %s; shipping without a gate", request.tool_call.get("name"))
            return self._append_note(await handler(request), "\n\n" + self._gate_failure_note("no thread in scope"))

        try:
            report = await self._run_gate_async(thread_id, self._get_user_id(request))
        except Exception as exc:  # noqa: BLE001 - fail open, never brick Nova
            logger.warning("[SecureShip] gate failed for thread=%s: %s", thread_id, exc, exc_info=True)
            return self._append_note(await handler(request), "\n\n" + self._gate_failure_note(str(exc) or exc.__class__.__name__))

        self._emit_event(thread_id, report)
        outcome, text = self._outcome(report)
        if outcome == "block":
            logger.warning("[SecureShip] BLOCKED thread=%s tool=%s %s", thread_id, request.tool_call.get("name"), text)
            return self._build_block_message(request, text)
        return self._append_note(await handler(request), "\n\n" + text)
