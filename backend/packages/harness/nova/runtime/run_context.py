"""Run-scoped identity for logs and observations.

Mirrors the ``byok_context`` pattern (a task-local ``ContextVar`` the worker
sets at run start) but answers a different question: *which run is writing
this line?* Before this module existed, a late exception in sandbox.log or
a warning from the token middleware could not be tied to a run — the exact
gap that made the 2026-11-01 incident (run 3cc50519) hard to reconstruct:
68M tokens were counted but nothing was attributable.

Authors
------
Writer side: ``runtime.runs.worker`` (``run_agent``) sets the frame once
per run, before any callback or tool can fire. ``asyncio.create_task``
copies the current context, so every task spawned by the run (ACP turns,
subagent polls, sandbox streams) sees the same frame.

Reader side: any harness code that emits an observable record — sandbox.log
JSON lines, ACP transport logs, the token-usage middleware — reads
:func:`current_run_fields` and stamps only the fields that exist. Nothing
here is required for a run to work; readers degrade to omitting the field
when no frame is set (embedded clients, tests).

Deliberately kept in the harness layer: consumers under
``packages/harness/deerflow`` must be able to import it without crossing
into ``app.gateway``, per the harness-boundary rule.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RunScope:
    """The identity of the run the current task belongs to.

    All three fields are optional because every caller site is optional:
    a tool invoked through some path the worker did not stamp should read
    ``None`` and simply omit the field.
    """

    run_id: str | None = None
    thread_id: str | None = None
    correlation_id: str | None = None

    # next: kept so a middleware can wrap a nested run (e.g. a bootstrap
    # title-generation run) and restore the outer frame afterwards without
    # reassembling the fields by hand.
    def with_overrides(self, **fields: str | None) -> RunScope:
        return replace(self, **fields)  # type: ignore[arg-type]


_scope: ContextVar[RunScope | None] = ContextVar("deerflow_run_scope", default=None)


def set_run_scope(
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    correlation_id: str | None = None,
) -> Token[RunScope | None]:
    """Set this task's run identity. Returns the token for :func:`reset_run_scope`."""
    return _scope.set(RunScope(run_id=run_id, thread_id=thread_id, correlation_id=correlation_id))


def reset_run_scope(token: Token[RunScope | None]) -> None:
    """Restore the previous frame — the worker's ``finally`` calls this."""
    _scope.reset(token)


def current_run_scope() -> RunScope | None:
    """The current task's run scope, or ``None`` outside a stamped run."""
    return _scope.get()


def current_run_fields() -> dict[str, str]:
    """Stamp-ready fields: only the ones actually known.

    Consumers merge this into their record/log calls. A missing field is
    omitted, not ``None``-padded, so log lines and JSON records stay byte
    identical whenever the context is not set — existing parsers and the
    frontend contract see no change on paths the worker never stamps.
    """
    scope = _scope.get()
    if scope is None:
        return {}
    fields: dict[str, str] = {}
    if scope.run_id:
        fields["run_id"] = scope.run_id
    if scope.thread_id:
        fields["thread_id"] = scope.thread_id
    if scope.correlation_id:
        fields["correlation_id"] = scope.correlation_id
    return fields


def format_scope() -> str:
    """One-line suffix for log messages, e.g. ``run_id=<...> thread_id=<...>``.

    Returns an empty string when no scope is set so callers can use it
    unconditionally in a format: ``logger.info("acp ... %s", format_scope())``
    prints nothing extra rather than the word ``None``.
    """
    fields = current_run_fields()
    if not fields:
        return ""
    order = ("run_id", "correlation_id", "thread_id")
    return " ".join(f"{k}={fields[k]}" for k in order if k in fields)
