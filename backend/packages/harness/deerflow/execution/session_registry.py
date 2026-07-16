"""Session Registry for the Nova Execution Kernel.

Phase C8 — tracks all interactive shell sessions with full lifecycle management.
Each session owns: PTY, stdin/stdout/stderr streams, working directory,
environment, terminal size, heartbeat, and metrics.

Usage::

    from deerflow.execution.session_registry import SessionRegistry, ShellSession

    registry = SessionRegistry()
    session = registry.create_session(run_id="run-1", ...)
    registry.heartbeat(session.session_id)
    # ... interactive I/O ...
    registry.close_session(session.session_id)
"""

from __future__ import annotations

import asyncio
import logging
import os
import select
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_session_id() -> str:
    import uuid
    return uuid.uuid4().hex


class SessionState(str, Enum):
    """Lifecycle states of an interactive shell session."""

    ALLOCATED = "allocated"    # PTY allocated, not yet spawned
    STARTING = "starting"      # Process starting
    RUNNING = "running"       # Active, accepting I/O
    WAITING = "waiting"        # Waiting for input
    STOPPING = "stopping"     # Graceful shutdown in progress
    STOPPED = "stopped"       # Confirmed stopped
    ZOMBIE = "zombie"          # Process alive, session orphaned
    ERROR = "error"           # Error state


@dataclass(frozen=True)
class TerminalSize:
    """Terminal dimensions for a shell session."""

    rows: int = 24
    cols: int = 80
    width_pixels: int = 0
    height_pixels: int = 0


@dataclass
class ShellSession:
    """A managed interactive shell session.

    Phase C8: tracks the full lifecycle of an interactive PTY session including
    PTY file descriptors, process ID, working directory, environment,
    terminal size, heartbeat, and I/O buffers.
    """

    session_id: str
    execution_id: str
    run_id: str
    thread_id: str = ""

    # PTY
    master_fd: int = -1
    slave_fd: int = -1
    pid: int = 0

    # Lifecycle
    state: SessionState = SessionState.ALLOCATED
    created_at: str = field(default_factory=_now_iso)
    started_at: str = ""
    last_heartbeat: float = field(default_factory=time.monotonic)

    # Terminal
    terminal_size: TerminalSize = field(default_factory=TerminalSize)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)

    # I/O tracking
    bytes_written: int = 0
    bytes_read: int = 0
    last_output_at: float = 0.0

    # Error state
    error: str = ""

    @property
    def is_alive(self) -> bool:
        return self.state in (SessionState.RUNNING, SessionState.WAITING)

    @property
    def age_seconds(self) -> float:
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return (datetime.now(UTC) - created).total_seconds()


class SessionRegistry:
    """Registry for all interactive shell sessions.

    Phase C8: provides centralized session lifecycle management, heartbeat
    tracking, and zombie detection for all PTY sessions.

    Thread-safe: all public methods acquire the internal lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, ShellSession] = {}
        self._sessions_by_execution: dict[str, str] = {}  # execution_id → session_id
        self._sessions_by_run: dict[str, str] = {}  # run_id → session_id

    def create_session(
        self,
        execution_id: str,
        run_id: str,
        *,
        thread_id: str = "",
        master_fd: int = -1,
        slave_fd: int = -1,
        pid: int = 0,
        cwd: str = "",
        env: dict[str, str] | None = None,
        terminal_size: TerminalSize | None = None,
    ) -> ShellSession:
        """Register a new interactive shell session.

        Args:
            execution_id: Associated execution ID.
            run_id: Associated run ID.
            thread_id: Associated thread ID.
            master_fd: PTY master file descriptor.
            slave_fd: PTY slave file descriptor.
            pid: Operating system process ID.
            cwd: Current working directory.
            env: Environment variables.
            terminal_size: Initial terminal dimensions.

        Returns:
            The created ShellSession.
        """
        session_id = _new_session_id()
        session = ShellSession(
            session_id=session_id,
            execution_id=execution_id,
            run_id=run_id,
            thread_id=thread_id,
            master_fd=master_fd,
            slave_fd=slave_fd,
            pid=pid,
            cwd=cwd or os.getcwd(),
            env=env if env is not None else dict(os.environ),
            terminal_size=terminal_size or TerminalSize(),
        )

        with self._lock:
            self._sessions[session_id] = session
            self._sessions_by_execution[execution_id] = session_id
            self._sessions_by_run[run_id] = session_id

        logger.info(
            "Session created: %s (execution=%s, run=%s, pid=%s)",
            session_id,
            execution_id,
            run_id,
            pid,
        )
        return session

    def get(self, session_id: str) -> ShellSession | None:
        """Get a session by session_id."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_by_execution(self, execution_id: str) -> ShellSession | None:
        """Get a session by execution_id."""
        with self._lock:
            session_id = self._sessions_by_execution.get(execution_id)
            return self._sessions.get(session_id) if session_id else None

    def get_by_run(self, run_id: str) -> ShellSession | None:
        """Get a session by run_id."""
        with self._lock:
            session_id = self._sessions_by_run.get(run_id)
            return self._sessions.get(session_id) if session_id else None

    def update_state(self, session_id: str, state: SessionState, error: str = "") -> None:
        """Update the lifecycle state of a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.state = state
            session.error = error
            if state == SessionState.RUNNING and not session.started_at:
                session.started_at = _now_iso()

    def heartbeat(
        self,
        session_id: str,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        last_output_at: float | None = None,
    ) -> None:
        """Update heartbeat for a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.last_heartbeat = time.monotonic()
            if bytes_read:
                session.bytes_read += bytes_read
            if bytes_written:
                session.bytes_written += bytes_written
            if last_output_at is not None:
                session.last_output_at = last_output_at

    def update_terminal_size(self, session_id: str, rows: int, cols: int) -> None:
        """Update terminal size for a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.terminal_size = TerminalSize(rows=rows, cols=cols)

    def close_session(self, session_id: str, reason: str = "") -> None:
        """Close and unregister a session.

        Does NOT kill the process — only removes session from registry.
        Caller is responsible for process termination via supervisor.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            self._sessions_by_execution.pop(session.execution_id, None)
            self._sessions_by_run.pop(session.run_id, None)

        logger.info(
            "Session closed: %s (reason=%s, bytes_written=%s, bytes_read=%s)",
            session_id,
            reason,
            session.bytes_written,
            session.bytes_read,
        )

    def scan_zombies(self, heartbeat_timeout: float = 30.0) -> list[str]:
        """Find sessions with stale heartbeats (possible zombies).

        Args:
            heartbeat_timeout: Seconds without heartbeat to consider zombie.

        Returns:
            List of zombie session_ids.
        """
        with self._lock:
            now = time.monotonic()
            zombies = []
            for session_id, session in self._sessions.items():
                if session.state in (SessionState.STOPPED, SessionState.ZOMBIE):
                    continue
                if now - session.last_heartbeat > heartbeat_timeout:
                    zombies.append(session_id)
            return zombies

    def list_active(self) -> list[ShellSession]:
        """Return all active sessions (running or waiting)."""
        with self._lock:
            return [s for s in self._sessions.values() if s.is_alive]

    def snapshot(self) -> dict[str, Any]:
        """Return full registry snapshot for diagnostics."""
        with self._lock:
            now = time.monotonic()
            active_count = sum(1 for s in self._sessions.values() if s.is_alive)
            return {
                "total_sessions": len(self._sessions),
                "active_sessions": active_count,
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "execution_id": s.execution_id,
                        "run_id": s.run_id,
                        "pid": s.pid,
                        "state": s.state.value,
                        "age_seconds": round(s.age_seconds, 1),
                        "heartbeat_age_seconds": round(now - s.last_heartbeat, 2),
                        "terminal_size": {
                            "rows": s.terminal_size.rows,
                            "cols": s.terminal_size.cols,
                        },
                        "cwd": s.cwd,
                        "bytes_written": s.bytes_written,
                        "bytes_read": s.bytes_read,
                        "error": s.error,
                    }
                    for s in self._sessions.values()
                ],
            }
