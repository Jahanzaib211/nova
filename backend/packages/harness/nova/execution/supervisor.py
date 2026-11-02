"""Supervisor for the Nova Execution Kernel.

Phase C8 — tracks every OS process the kernel creates, enforces graceful
termination (TERM → grace period → KILL), supports cancellation by
execution id, and reconciles orphans at shutdown or on demand.

Two process shapes are supervised:

- Short-lived ``subprocess.Popen`` objects (the kernel's sync engine).
- Long-running ``asyncio.subprocess.Process`` objects (``kernel.spawn``),
  wrapped in :class:`SpawnedProcess` handles.

Phase C8 additionally provides:

- ExecutionID ↔ RunID ↔ SessionID bidirectional ownership maps.
- Per-execution heartbeat tracking with zombie detection.
- Deterministic cancellation protocol with child-tree cleanup.
- PTY session tracking for interactive shell sessions.

The supervisor never creates processes — only the kernel does.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal as _signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heartbeat tracking
# ---------------------------------------------------------------------------


@dataclass
class ProcessHeartbeat:
    """Latest heartbeat from a supervised process."""

    execution_id: str
    pid: int
    timestamp: float = field(default_factory=time.monotonic)
    stdin_alive: bool = True
    pty_alive: bool = False
    cpu_percent: float = 0.0
    memory_mb: float = 0.0


def _signal_process_tree(pid: int, sig: int, fallback: Any) -> None:
    """Signal *pid*'s process group when it leads its own group, else *pid*.

    Kernel-created processes use ``start_new_session=True`` so children
    (e.g. ``sleep`` spawned by ``sh -c``) die with their parent — otherwise
    orphaned grandchildren keep pipes open and hold ports.  Guarded so a
    process sharing OUR group is never group-signalled.
    """
    if os.name == "posix":
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgid(0):
                os.killpg(pgid, sig)
                return
        except (ProcessLookupError, PermissionError, OSError):
            return  # process already gone (or not signalable)
    with contextlib.suppress(ProcessLookupError, OSError):
        fallback()


# ---------------------------------------------------------------------------
# Spawned (long-running) process handle
# ---------------------------------------------------------------------------


@dataclass
class SpawnedProcess:
    """Supervised handle around a long-running asyncio subprocess.

    Exposes the minimal surface long-running callers (dev servers) need:
    stdout streaming, exit inspection, and graceful termination.  Callers
    never touch signals or ``kill()`` directly.
    """

    execution_id: str
    execution_class: str
    proc: asyncio.subprocess.Process
    argv: tuple[str, ...] = ()
    started_at: float = field(default_factory=time.monotonic)
    grace_period: float = 5.0

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self.proc.stdout

    async def wait(self) -> int:
        return await self.proc.wait()

    async def terminate_gracefully(self, grace_period: float | None = None) -> int | None:
        """TERM, wait up to the grace period, then KILL.  Returns exit code.

        Signals the whole process group (kernel spawns are session leaders)
        so grandchildren die too.
        """
        grace = self.grace_period if grace_period is None else grace_period
        if self.proc.returncode is None:
            _signal_process_tree(self.proc.pid, _signal.SIGTERM, self.proc.terminate)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=grace)
            except TimeoutError:
                _signal_process_tree(self.proc.pid, _signal.SIGKILL, self.proc.kill)
                with contextlib.suppress(Exception):
                    await self.proc.wait()
        return self.proc.returncode


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    """Registry + lifecycle enforcement for kernel-created processes.

    Phase C8 adds:
    - Bidirectional ownership maps: execution_id ↔ run_id, execution_id ↔ session_id
    - Per-execution heartbeat tracking for zombie detection
    - Child-process tree tracking for recursive cancellation
    - PTY session registry for interactive shell support
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._popen: dict[str, subprocess.Popen[Any]] = {}
        self._spawned: dict[str, SpawnedProcess] = {}
        self._cancelled: set[str] = set()

        # Phase C8: ownership maps
        self._execution_to_run: dict[str, str] = {}  # execution_id → run_id
        self._run_to_execution: dict[str, str] = {}  # run_id → execution_id
        self._execution_to_session: dict[str, str] = {}  # execution_id → session_id
        self._session_to_execution: dict[str, str] = {}  # session_id → execution_id
        self._execution_to_pid: dict[str, int] = {}  # execution_id → pid

        # Phase C8: heartbeat tracking
        self._heartbeats: dict[str, ProcessHeartbeat] = {}

        # Phase C8: child tree tracking (execution_id → set of child execution_ids)
        self._children: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Ownership maps (Phase C8)
    # ------------------------------------------------------------------

    def register_execution(
        self,
        execution_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        pid: int = 0,
        parent_execution_id: str = "",
    ) -> None:
        """Register execution ownership metadata.

        Args:
            execution_id: Unique execution identifier.
            run_id: Associated run ID (from RunManager).
            session_id: Associated shell session ID (from SessionRegistry).
            pid: Operating system process ID.
            parent_execution_id: Parent execution that spawned this one.
        """
        with self._lock:
            if run_id:
                self._execution_to_run[execution_id] = run_id
                self._run_to_execution[run_id] = execution_id
            if session_id:
                self._execution_to_session[execution_id] = session_id
                self._session_to_execution[session_id] = execution_id
            if pid:
                self._execution_to_pid[execution_id] = pid
            if parent_execution_id:
                if parent_execution_id not in self._children:
                    self._children[parent_execution_id] = set()
                self._children[parent_execution_id].add(execution_id)

    def unregister_execution(self, execution_id: str) -> None:
        """Remove all ownership metadata for an execution.

        Also removes from parent's child list and cascades to children.
        """
        with self._lock:
            # Remove from parent's child list
            for parent_eid, children in self._children.items():
                children.discard(execution_id)

            # Cascade unregister to all children (recursive cancellation)
            children_to_remove = list(self._children.get(execution_id, set()))
            for child_eid in children_to_remove:
                self.unregister_execution(child_eid)

            # Clean up maps
            run_id = self._execution_to_run.pop(execution_id, None)
            if run_id:
                self._run_to_execution.pop(run_id, None)

            session_id = self._execution_to_session.pop(execution_id, None)
            if session_id:
                self._session_to_execution.pop(session_id, None)

            self._execution_to_pid.pop(execution_id, None)
            self._cancelled.discard(execution_id)
            self._heartbeats.pop(execution_id, None)
            self._spawned.pop(execution_id, None)
            self._popen.pop(execution_id, None)
            self._children.pop(execution_id, None)

    def get_execution_id(self, run_id: str) -> str | None:
        """Look up execution_id by run_id."""
        with self._lock:
            return self._run_to_execution.get(run_id)

    def get_run_id(self, execution_id: str) -> str | None:
        """Look up run_id by execution_id."""
        with self._lock:
            return self._execution_to_run.get(execution_id)

    def get_session_id(self, execution_id: str) -> str | None:
        """Look up shell session ID by execution_id."""
        with self._lock:
            return self._execution_to_session.get(execution_id)

    def get_execution_id_by_session(self, session_id: str) -> str | None:
        """Look up execution_id by shell session ID."""
        with self._lock:
            return self._session_to_execution.get(session_id)

    def get_pid(self, execution_id: str) -> int | None:
        """Look up operating system PID by execution_id."""
        with self._lock:
            return self._execution_to_pid.get(execution_id)

    def get_children(self, execution_id: str) -> set[str]:
        """Return set of child execution IDs."""
        with self._lock:
            return set(self._children.get(execution_id, set()))

    # ------------------------------------------------------------------
    # Heartbeat (Phase C8)
    # ------------------------------------------------------------------

    def update_heartbeat(self, execution_id: str, **kwargs: Any) -> None:
        """Update heartbeat for an execution.

        Args:
            execution_id: Execution to heartbeat.
            kwargs: Heartbeat fields (stdin_alive, pty_alive, cpu_percent, memory_mb).
        """
        with self._lock:
            pid = self._execution_to_pid.get(execution_id, 0)
            existing = self._heartbeats.get(execution_id)
            self._heartbeats[execution_id] = ProcessHeartbeat(
                execution_id=execution_id,
                pid=pid,
                timestamp=time.monotonic(),
                stdin_alive=kwargs.get("stdin_alive", existing.stdin_alive if existing else True),
                pty_alive=kwargs.get("pty_alive", existing.pty_alive if existing else False),
                cpu_percent=kwargs.get("cpu_percent", existing.cpu_percent if existing else 0.0),
                memory_mb=kwargs.get("memory_mb", existing.memory_mb if existing else 0.0),
            )

    def get_heartbeat(self, execution_id: str) -> ProcessHeartbeat | None:
        """Get latest heartbeat for an execution."""
        with self._lock:
            return self._heartbeats.get(execution_id)

    def scan_zombies(self, heartbeat_timeout: float = 30.0) -> list[str]:
        """Scan for executions with stale heartbeats (possible zombies).

        Args:
            heartbeat_timeout: Seconds without heartbeat to consider zombie.

        Returns:
            List of execution_ids that may be zombies.
        """
        with self._lock:
            now = time.monotonic()
            zombies = []
            for execution_id in self._execution_to_pid.keys():
                hb = self._heartbeats.get(execution_id)
                proc = self._popen.get(execution_id)
                spawned = self._spawned.get(execution_id)

                # If process is still tracked but heartbeat is stale, flag as zombie
                if hb is None and (proc is not None or spawned is not None):
                    # No heartbeat ever recorded — check if process is actually alive
                    if proc is not None and proc.poll() is None:
                        zombies.append(execution_id)
                    elif spawned is not None and spawned.returncode is None:
                        zombies.append(execution_id)
                elif hb is not None:
                    # Heartbeat exists but is stale
                    if now - hb.timestamp > heartbeat_timeout:
                        # Check if process is still alive
                        if proc is not None and proc.poll() is None:
                            zombies.append(execution_id)
                        elif spawned is not None and spawned.returncode is None:
                            zombies.append(execution_id)
            return zombies

    # ------------------------------------------------------------------
    # Registration (kernel-internal)
    # ------------------------------------------------------------------

    def register_popen(
        self,
        execution_id: str,
        proc: subprocess.Popen[Any],
        *,
        run_id: str = "",
        pid: int = 0,
        parent_execution_id: str = "",
    ) -> None:
        with self._lock:
            self._popen[execution_id] = proc
            if run_id:
                self._execution_to_run[execution_id] = run_id
                self._run_to_execution[run_id] = execution_id
            actual_pid = pid or proc.pid
            if actual_pid:
                self._execution_to_pid[execution_id] = actual_pid
            if parent_execution_id:
                if parent_execution_id not in self._children:
                    self._children[parent_execution_id] = set()
                self._children[parent_execution_id].add(execution_id)

    def unregister_popen(self, execution_id: str) -> None:
        with self._lock:
            self._popen.pop(execution_id, None)
            self._cancelled.discard(execution_id)
            self._execution_to_pid.pop(execution_id, None)
            self._heartbeats.pop(execution_id, None)

    def register_spawned(
        self,
        handle: SpawnedProcess,
        *,
        run_id: str = "",
        session_id: str = "",
        parent_execution_id: str = "",
    ) -> None:
        with self._lock:
            self._spawned[handle.execution_id] = handle
            if run_id:
                self._execution_to_run[handle.execution_id] = run_id
                self._run_to_execution[run_id] = handle.execution_id
            self._execution_to_pid[handle.execution_id] = handle.pid
            if session_id:
                self._execution_to_session[handle.execution_id] = session_id
                self._session_to_execution[session_id] = handle.execution_id
            if parent_execution_id:
                if parent_execution_id not in self._children:
                    self._children[parent_execution_id] = set()
                self._children[parent_execution_id].add(handle.execution_id)

    def unregister_spawned(self, execution_id: str) -> None:
        with self._lock:
            self._spawned.pop(execution_id, None)
            self._cancelled.discard(execution_id)
            self._execution_to_pid.pop(execution_id, None)
            self._heartbeats.pop(execution_id, None)

    # ------------------------------------------------------------------
    # Cancellation (Phase C8: recursive child cancellation)
    # ------------------------------------------------------------------

    def cancel(self, execution_id: str, grace_period: float = 5.0, *, recursive: bool = True) -> bool:
        """Cancel a running execution by id.  Returns True if found.

        Phase C8: when recursive=True (default), cancels all descendant
        executions in the child tree before cancelling the target.
        """
        cancelled_any = False

        # Cancel children first (deepest first)
        if recursive:
            with self._lock:
                children = list(self._children.get(execution_id, set()))
            for child_eid in children:
                if self.cancel(child_eid, grace_period, recursive=True):
                    cancelled_any = True

        # Cancel the target itself
        with self._lock:
            proc = self._popen.get(execution_id)
        if proc is not None:
            with self._lock:
                self._cancelled.add(execution_id)
            self.terminate_popen(proc, grace_period)
            return True

        with self._lock:
            handle = self._spawned.get(execution_id)
        if handle is not None:
            self._schedule_async_termination(handle, grace_period)
            return True

        return cancelled_any

    def was_cancelled(self, execution_id: str) -> bool:
        with self._lock:
            return execution_id in self._cancelled

    # ------------------------------------------------------------------
    # Orphan reconciliation / shutdown
    # ------------------------------------------------------------------

    def reconcile_orphans(self, grace_period: float = 5.0) -> int:
        """Terminate every process the kernel still tracks.  Returns count.

        Called on gateway shutdown so no kernel-created process outlives
        the platform.
        """
        with self._lock:
            popen_items = list(self._popen.items())
            spawned_items = list(self._spawned.items())

        count = 0
        for execution_id, proc in popen_items:
            if proc.poll() is None:
                logger.warning("Reconciling orphan popen %s (pid %s)", execution_id, proc.pid)
                self.terminate_popen(proc, grace_period)
                count += 1
            self.unregister_popen(execution_id)

        for execution_id, handle in spawned_items:
            if handle.returncode is None:
                logger.warning(
                    "Reconciling orphan spawned process %s (pid %s)",
                    execution_id,
                    handle.pid,
                )
                self._schedule_async_termination(handle, grace_period)
                count += 1
            self.unregister_spawned(execution_id)
        return count

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "popen_tracked": len(self._popen),
                "spawned_tracked": len(self._spawned),
                "execution_to_run": dict(self._execution_to_run),
                "run_to_execution": dict(self._run_to_execution),
                "execution_to_session": dict(self._execution_to_session),
                "session_to_execution": dict(self._session_to_execution),
                "tracked_pids": dict(self._execution_to_pid),
                "heartbeats": {
                    eid: {
                        "timestamp": hb.timestamp,
                        "age_seconds": round(now - hb.timestamp, 2),
                        "stdin_alive": hb.stdin_alive,
                        "pty_alive": hb.pty_alive,
                        "cpu_percent": round(hb.cpu_percent, 1),
                        "memory_mb": round(hb.memory_mb, 1),
                    }
                    for eid, hb in self._heartbeats.items()
                },
                "child_trees": {parent: list(children) for parent, children in self._children.items()},
                "cancelled": list(self._cancelled),
                "spawned": [
                    {
                        "execution_id": h.execution_id,
                        "class": h.execution_class,
                        "pid": h.pid,
                        "returncode": h.returncode,
                        "argv": list(h.argv),
                    }
                    for h in self._spawned.values()
                ],
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def terminate_popen(proc: subprocess.Popen[Any], grace_period: float) -> None:
        """TERM the process tree, wait up to the grace period, then KILL."""
        if proc.poll() is not None:
            return
        _signal_process_tree(proc.pid, _signal.SIGTERM, proc.terminate)
        try:
            proc.wait(timeout=grace_period)
        except subprocess.TimeoutExpired:
            _signal_process_tree(proc.pid, _signal.SIGKILL, proc.kill)
            with contextlib.suppress(Exception):
                proc.wait(timeout=grace_period)

    @staticmethod
    def _schedule_async_termination(handle: SpawnedProcess, grace_period: float) -> None:
        """Terminate a spawned process from any thread.

        If an event loop is running in this thread, schedule the graceful
        path; otherwise fall back to synchronous signal escalation.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(handle.terminate_gracefully(grace_period))
            return
        proc = handle.proc
        if proc.returncode is None:
            _signal_process_tree(proc.pid, _signal.SIGTERM, proc.terminate)
            deadline = time.monotonic() + grace_period
            while time.monotonic() < deadline and proc.returncode is None:
                time.sleep(0.1)
            if proc.returncode is None:
                _signal_process_tree(proc.pid, _signal.SIGKILL, proc.kill)
