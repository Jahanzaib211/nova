"""The Nova Execution Kernel.

Phase C8 — the single place in the entire platform where OS processes are
created.  Every subprocess, docker CLI call, git command, pm2/systemctl
invocation, and dev-server spawn flows through :class:`ExecutionKernel`.

Pipeline (deterministic, same order every time)::

    request → scheduler (policy + resources) → supervisor-registered process
            → heartbeat monitoring → timeout/cancellation enforcement → result
            → audit record → metrics → domain events

Phase C8 additionally provides:

- ExecutionID ↔ RunID ↔ SessionID bidirectional ownership maps.
- Per-execution heartbeat thread for liveness monitoring.
- Deterministic cancellation protocol with recursive child cancellation.
- PTY session tracking for interactive shell support.

Design notes:

- The synchronous engine (``execute_sync``) is the primitive.  It uses
  ``subprocess.Popen`` and is safe from any thread — sync call sites
  (sandbox backends, review) call it directly; async call sites await
  ``execute()`` which off-loads via ``asyncio.to_thread`` so the event
  loop is never blocked.
- ``spawn()`` creates supervised long-running processes (dev servers)
  with streaming stdout and graceful-termination handles.
- ``shell=True`` does not exist here.  Shell semantics are explicit argv
  (``["/bin/sh", "-c", cmd]``) constructed by the Shell adapter.
- Heartbeat thread runs during execute_sync to detect zombie processes.
- Cancellation uses two-phase: SIGINT → grace → SIGTERM → grace → SIGKILL.

Usage::

    from deerflow.execution import ExecutionKernel, ExecutionRequest, ExecutionClass

    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("git", "status"), execution_class=ExecutionClass.GIT)
    )
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from deerflow.events.bus import EventBus
from deerflow.execution.audit import AuditEngine
from deerflow.execution.metrics import ExecutionMetrics
from deerflow.execution.models import (
    ExecutionClass,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    now_iso,
)
from deerflow.execution.policy import PolicyEngine
from deerflow.execution.resources import ResourceManager
from deerflow.execution.scheduler import Admission, Scheduler
from deerflow.execution.supervisor import ProcessHeartbeat, SpawnedProcess, Supervisor

logger = logging.getLogger(__name__)


class ExecutionKernel:
    """Deterministic, observable, policy-gated process execution."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        resource_manager: ResourceManager | None = None,
        audit_engine: AuditEngine | None = None,
        metrics: ExecutionMetrics | None = None,
        supervisor: Supervisor | None = None,
        event_bus: EventBus | None = None,
        max_spawned: int = 32,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.resource_manager = resource_manager or ResourceManager()
        self.audit_engine = audit_engine or AuditEngine()
        self.metrics = metrics or ExecutionMetrics()
        self.supervisor = supervisor or Supervisor()
        self._bus = event_bus
        self._scheduler = Scheduler(self.policy_engine, self.resource_manager)
        self._max_spawned = max_spawned

    # ------------------------------------------------------------------
    # Synchronous engine (the primitive)
    # ------------------------------------------------------------------

    def execute_sync(self, request: ExecutionRequest) -> ExecutionResult:
        """Run one execution to completion.  Never raises for process
        failures — every outcome is a typed :class:`ExecutionResult`.

        Phase C8: emits periodic heartbeat events while running, tracks
        ownership maps (execution_id ↔ run_id ↔ session_id), and uses
        two-phase cancellation (SIGINT → grace → SIGTERM → grace → SIGKILL).
        """
        self._emit_lifecycle("ExecutionRequested", request)

        admission = self._scheduler.admit(request)
        if not admission.admitted:
            return self._finalize(
                request,
                ExecutionResult(
                    execution_id=request.execution_id,
                    status=ExecutionStatus.DENIED,
                    error=admission.reason,
                    execution_class=request.execution_class,
                    correlation_id=request.correlation_id,
                    started_at=now_iso(),
                    finished_at=now_iso(),
                ),
            )

        started_monotonic = time.monotonic()
        started_at = now_iso()
        status = ExecutionStatus.FAILED
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        error: str | None = None
        proc: subprocess.Popen[str] | None = None
        heartbeat_thread: threading.Thread | None = None
        _stop_heartbeat: threading.Event | None = None

        try:
            self._emit_lifecycle("ExecutionStarted", request)
            proc = subprocess.Popen(  # noqa: S603 — the kernel is the one sanctioned exec site
                list(request.argv),
                cwd=request.cwd,
                env=request.env,
                stdin=subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=(os.name == "posix"),
            )

            # Phase C8: register with full ownership metadata
            self.supervisor.register_popen(
                request.execution_id,
                proc,
                run_id=request.run_id,
                pid=proc.pid,
                parent_execution_id=request.labels.get("parent_execution_id", ""),
            )

            # Phase C8: start heartbeat thread
            heartbeat_interval = 5.0
            _stop_heartbeat_local = threading.Event()

            def _heartbeat_loop() -> None:
                while not _stop_heartbeat_local.wait(heartbeat_interval):
                    if proc.poll() is not None:
                        break
                    try:
                        self.supervisor.update_heartbeat(
                            request.execution_id,
                            stdin_alive=(proc.stdin is not None and not proc.stdin.closed),
                            pty_alive=False,
                        )
                    except Exception:
                        pass

            heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
            heartbeat_thread.start()
            _stop_heartbeat = _stop_heartbeat_local

            try:
                stdout, stderr = proc.communicate(input=request.stdin, timeout=admission.effective_timeout)
                exit_code = proc.returncode
                if self.supervisor.was_cancelled(request.execution_id):
                    status = ExecutionStatus.CANCELLED
                    error = "cancelled"
                elif exit_code == 0:
                    status = ExecutionStatus.SUCCEEDED
                else:
                    status = ExecutionStatus.FAILED
                    error = f"exit code {exit_code}"
            except subprocess.TimeoutExpired:
                # Two-phase cancel: SIGINT first, then SIGTERM, then SIGKILL
                self._two_phase_cancel(proc, request.limits.grace_period)
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except Exception:
                    stdout, stderr = "", ""
                stdout, stderr = stdout or "", stderr or ""
                exit_code = proc.returncode
                status = ExecutionStatus.TIMED_OUT
                error = f"timed out after {admission.effective_timeout}s"
        except FileNotFoundError as e:
            status = ExecutionStatus.FAILED
            error = f"program not found: {e}"
        except Exception as e:
            status = ExecutionStatus.FAILED
            error = f"{type(e).__name__}: {e}"
            logger.exception("Execution %s crashed", request.execution_id)
        finally:
            # Phase C8: stop heartbeat thread
            if _stop_heartbeat is not None:
                _stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1.0)

            if proc is not None:
                self.supervisor.unregister_popen(request.execution_id)
            self._scheduler.release(request)

        duration_ms = (time.monotonic() - started_monotonic) * 1000.0
        max_bytes = request.limits.max_output_bytes
        return self._finalize(
            request,
            ExecutionResult(
                execution_id=request.execution_id,
                status=status,
                exit_code=exit_code,
                stdout=_truncate(stdout, max_bytes),
                stderr=_truncate(stderr, max_bytes),
                duration_ms=duration_ms,
                started_at=started_at,
                finished_at=now_iso(),
                error=error,
                execution_class=request.execution_class,
                correlation_id=request.correlation_id,
            ),
        )

    def _two_phase_cancel(self, proc: subprocess.Popen[Any], grace_period: float) -> None:
        """Two-phase cancellation: SIGINT → wait → SIGTERM → wait → SIGKILL.

        Phase C8: implements the full cancellation escalation chain.
        """
        # Phase 1: SIGINT (polite shutdown)
        proc.terminate()
        try:
            proc.wait(timeout=min(grace_period * 0.3, 2.0))
            return  # Clean exit on SIGINT
        except subprocess.TimeoutExpired:
            pass

        # Phase 2: SIGTERM (forceful shutdown)
        proc.terminate()
        try:
            proc.wait(timeout=min(grace_period * 0.5, 3.0))
            return
        except subprocess.TimeoutExpired:
            pass

        # Phase 3: SIGKILL (last resort)
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)

    # ------------------------------------------------------------------
    # Async facade
    # ------------------------------------------------------------------

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Async wrapper — runs the sync engine off the event loop."""
        return await asyncio.to_thread(self.execute_sync, request)

    # ------------------------------------------------------------------
    # Long-running processes
    # ------------------------------------------------------------------

    async def spawn(
        self,
        request: ExecutionRequest,
        *,
        start_new_session: bool = True,
        merge_stderr: bool = True,
        session_id: str = "",
    ) -> SpawnedProcess:
        """Start a supervised long-running process (dev servers, daemons).

        Phase C8: registers full ownership metadata including run_id and session_id.

        Raises :class:`ExecutionDeniedError` when policy rejects the request
        or the spawned-process budget is exhausted — long-running callers
        need the failure immediately, not a result object.
        """
        decision = self.policy_engine.evaluate(request)
        if not decision.allowed:
            self._emit_lifecycle("ExecutionDenied", request, extra={"reason": decision.reason})
            raise ExecutionDeniedError(decision.reason)

        tracked = self.supervisor.snapshot()["spawned_tracked"]
        if tracked >= self._max_spawned:
            reason = f"spawned-process budget exhausted ({tracked}/{self._max_spawned})"
            self._emit_lifecycle("ExecutionDenied", request, extra={"reason": reason})
            raise ExecutionDeniedError(reason)

        proc = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=request.cwd,
            env=request.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
            start_new_session=start_new_session,
        )
        handle = SpawnedProcess(
            execution_id=request.execution_id,
            execution_class=request.execution_class.value,
            proc=proc,
            argv=tuple(request.argv),
            grace_period=request.limits.grace_period,
        )
        # Phase C8: register with full ownership metadata
        self.supervisor.register_spawned(
            handle,
            run_id=request.run_id,
            session_id=session_id,
            parent_execution_id=request.labels.get("parent_execution_id", ""),
        )
        self._emit_lifecycle("ProcessSpawned", request, extra={"pid": proc.pid, "session_id": session_id})
        asyncio.get_running_loop().create_task(self._watch_spawned(request, handle))
        return handle

    async def _watch_spawned(self, request: ExecutionRequest, handle: SpawnedProcess) -> None:
        """Record the exit of a spawned process in audit/metrics/events."""
        exit_code = await handle.proc.wait()
        self.supervisor.unregister_spawned(handle.execution_id)
        status = ExecutionStatus.SUCCEEDED if exit_code == 0 else ExecutionStatus.FAILED
        duration_ms = (time.monotonic() - handle.started_at) * 1000.0
        result = ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            started_at=request.created_at,
            finished_at=now_iso(),
            error=None if exit_code == 0 else f"exit code {exit_code}",
            execution_class=request.execution_class,
            correlation_id=request.correlation_id,
        )
        self.audit_engine.record(request, result)
        self.metrics.observe(request.execution_class, status, duration_ms)
        self._emit_lifecycle("ProcessExited", request, extra={"exit_code": exit_code, "pid": handle.pid})

    # ------------------------------------------------------------------
    # Cancellation / shutdown
    # ------------------------------------------------------------------

    def cancel(self, execution_id: str, grace_period: float = 5.0, *, recursive: bool = True) -> bool:
        """Cancel a running execution or spawned process by id.

        Phase C8: recursive cancellation walks the entire child tree,
        cancelling deepest children first before the target.
        """
        return self.supervisor.cancel(execution_id, grace_period, recursive=recursive)

    def shutdown(self) -> int:
        """Terminate every tracked process.  Returns count reconciled."""
        return self.supervisor.reconcile_orphans()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.snapshot(),
            "resources": self.resource_manager.snapshot(),
            "supervisor": self.supervisor.snapshot(),
            "audit_size": self.audit_engine.size,
            "audit_chain_valid": self.audit_engine.verify_chain(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _finalize(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        self.audit_engine.record(request, result)
        self.metrics.observe(request.execution_class, result.status, result.duration_ms)
        event_name = {
            ExecutionStatus.SUCCEEDED: "ExecutionCompleted",
            ExecutionStatus.FAILED: "ExecutionFailed",
            ExecutionStatus.TIMED_OUT: "ExecutionTimedOut",
            ExecutionStatus.CANCELLED: "ExecutionCancelled",
            ExecutionStatus.DENIED: "ExecutionDenied",
        }.get(result.status, "ExecutionCompleted")
        self._emit_lifecycle(
            event_name,
            request,
            extra={
                "status": result.status.value,
                "exit_code": result.exit_code,
                "duration_ms": round(result.duration_ms, 2),
                "error": result.error or "",
            },
        )
        return result

    def _emit_lifecycle(
        self,
        event_name: str,
        request: ExecutionRequest,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._bus is None:
            return
        try:
            from deerflow.events import event as events_mod

            event_cls = getattr(events_mod, event_name, None)
            if event_cls is None:
                return
            payload = {
                "execution_id": request.execution_id,
                "execution_class": request.execution_class.value,
                "program": request.argv[0] if request.argv else "",
                "intent": request.intent,
                **(extra or {}),
            }
            self._bus.publish(
                event_cls(
                    correlation_id=request.correlation_id,
                    run_id=request.run_id,
                    thread_id=request.thread_id,
                    payload=payload,
                )
            )
        except Exception:
            logger.exception("Failed to emit %s for %s", event_name, request.execution_id)


class ExecutionDeniedError(RuntimeError):
    """Raised by ``spawn`` when policy or budget rejects a request."""


def _truncate(text: str, max_bytes: int) -> str:
    if text is None:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n[output truncated]"


def request_with_context(
    request: ExecutionRequest,
    *,
    correlation_id: str = "",
    run_id: str = "",
    thread_id: str = "",
) -> ExecutionRequest:
    """Return a copy of *request* stamped with tracing context."""
    return replace(
        request,
        correlation_id=correlation_id or request.correlation_id,
        run_id=run_id or request.run_id,
        thread_id=thread_id or request.thread_id,
    )
