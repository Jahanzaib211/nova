"""Interactive Shell Adapter for the Nova Execution Kernel.

Phase C8 — provides persistent interactive PTY shell sessions with full
read/write/resize/heartbeat lifecycle management.

Design notes:

- Each session owns a PTY pair, a shell subprocess, and a SessionRegistry entry.
- Sessions persist until explicitly closed — commands do NOT exit the shell.
- Input/output are handled via non-blocking I/O with configurable buffers.
- Heartbeat is emitted every 5 seconds while session is active.
- Window resize sends SIGWINCH to the process group.

Usage::

    from deerflow.execution.adapters.interactive_shell import InteractiveShellAdapter
    from deerflow.execution.session_registry import SessionRegistry

    registry = SessionRegistry()
    adapter = InteractiveShellAdapter(registry=registry)

    session_id = adapter.create_session(run_id="run-1", correlation_id="corr-1")
    adapter.write(session_id, "echo hello\\n")
    output = adapter.read(session_id, timeout=5.0)
    adapter.resize(session_id, rows=40, cols=120)
    adapter.close_session(session_id)
"""

from __future__ import annotations

import asyncio
import logging
import os
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from deerflow.execution.models import ExecutionClass, ExecutionRequest
from deerflow.execution.pty_manager import PTYManager, TerminalSize
from deerflow.execution.session_registry import SessionRegistry, SessionState, ShellSession

logger = logging.getLogger(__name__)


@dataclass
class ReadResult:
    """Result of a non-blocking read from a shell session."""

    data: str
    bytes_read: int
    eof: bool = False
    timed_out: bool = False


class InteractiveShellAdapter:
    """Manages persistent interactive PTY shell sessions.

    Phase C8: replaces one-shot shell command execution with persistent sessions.
    Each session maintains a PTY, a shell subprocess, and a registered entry
    in the SessionRegistry with full lifecycle tracking.

    Thread-safe: uses SessionRegistry's internal lock plus per-session I/O locks.
    """

    def __init__(
        self,
        registry: SessionRegistry | None = None,
        pty_manager: PTYManager | None = None,
        heartbeat_interval: float = 5.0,
        default_shell: str = "/bin/sh",
    ) -> None:
        self._registry = registry or SessionRegistry()
        self._pty_manager = pty_manager or PTYManager()
        self._heartbeat_interval = heartbeat_interval
        self._default_shell = default_shell

        # Per-session I/O locks
        self._io_locks: dict[str, threading.Lock] = {}
        self._heartbeat_threads: dict[str, threading.Event] = {}

    def create_session(
        self,
        run_id: str,
        execution_id: str,
        *,
        correlation_id: str = "",
        thread_id: str = "",
        shell_path: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        terminal_size: TerminalSize | None = None,
        argv: tuple[str, ...] | None = None,
    ) -> str:
        """Create and start an interactive shell session.

        Args:
            run_id: Associated run ID.
            execution_id: Associated execution ID.
            correlation_id: Correlation ID for tracing.
            thread_id: Associated thread ID.
            shell_path: Path to shell executable.
            cwd: Initial working directory.
            env: Environment variables.
            terminal_size: Initial terminal dimensions.
            argv: Pre-built argv (e.g. ["/bin/bash", "-i"]). If None, uses shell_path.

        Returns:
            session_id: Unique session identifier.

        Raises:
            OSError: If PTY allocation or process spawn fails.
        """
        shell = shell_path or self._default_shell
        if argv is None:
            argv = (shell,)

        # Allocate PTY
        master_fd, slave_fd = self._pty_manager.open()

        # Set initial terminal size
        ts = terminal_size or TerminalSize(rows=24, cols=80)
        self._pty_manager.set_window_size(master_fd, ts.rows, ts.cols)

        # Spawn shell with PTY
        proc = subprocess.Popen(
            list(argv),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd or os.getcwd(),
            env=env,
            # start_new_session already performs setsid() in the child. Passing
            # preexec_fn=os.setsid as well called it a second time, which fails
            # with EPERM because the process is a session leader by then, so
            # Popen raised "Exception occurred in preexec_fn" and every session
            # creation died. That is the "known Phase C8 issue" the tests below
            # were skipped for. The rest of the codebase already uses the kwarg
            # alone (kernel.py, protocols.py).
            start_new_session=True,
        )

        # Close slave in parent — child has it duped
        os.close(slave_fd)

        # Make master non-blocking for async read/write
        import fcntl

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Create session in registry
        session = self._registry.create_session(
            execution_id=execution_id,
            run_id=run_id,
            thread_id=thread_id,
            master_fd=master_fd,
            slave_fd=-1,  # Already closed in parent
            pid=proc.pid,
            cwd=cwd or os.getcwd(),
            env=env,
            terminal_size=ts,
        )

        # Track per-session lock
        self._io_locks[session.session_id] = threading.Lock()
        self._registry.update_state(session.session_id, SessionState.RUNNING)

        # Start heartbeat thread
        self._start_heartbeat(session.session_id)

        logger.info(
            "Interactive shell session started: %s (pid=%s, shell=%s)",
            session.session_id,
            proc.pid,
            shell,
        )
        return session.session_id

    def write(self, session_id: str, data: str, timeout: float = 5.0) -> int:
        """Write data to a shell session's stdin.

        Args:
            session_id: Session to write to.
            data: String data to write.
            timeout: Maximum seconds to wait for write.

        Returns:
            Bytes written.

        Raises:
            OSError: If session not found or write fails.
        """
        session = self._registry.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        lock = self._io_locks.get(session_id)
        if lock is None:
            raise ValueError(f"No I/O lock for session: {session_id}")

        if not lock.acquire(timeout=timeout):
            raise TimeoutError(f"Could not acquire I/O lock for session: {session_id}")

        try:
            master_fd = session.master_fd
            if master_fd < 0:
                raise OSError(f"Invalid master_fd for session: {session_id}")

            encoded = data.encode("utf-8", errors="replace")
            written = 0
            # Write in loop to handle partial writes
            while written < len(encoded):
                try:
                    n = os.write(master_fd, encoded[written:])
                    if n < 0:
                        raise OSError(f"Write returned {n}")
                    written += n
                except BlockingIOError:
                    # Would block — wait and retry
                    time.sleep(0.01)
                    continue

            self._registry.heartbeat(session_id, bytes_written=written)
            return written
        finally:
            lock.release()

    def read(self, session_id: str, max_bytes: int = 8192, timeout: float = 5.0) -> ReadResult:
        """Read available output from a shell session.

        Uses non-blocking I/O with timeout. Returns whatever is currently
        available up to max_bytes.

        Args:
            session_id: Session to read from.
            max_bytes: Maximum bytes to read.
            timeout: Maximum seconds to wait for data.

        Returns:
            ReadResult with data, bytes_read, eof flag, timed_out flag.

        Raises:
            ValueError: If session not found.
        """
        session = self._registry.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        lock = self._io_locks.get(session_id)
        if lock is None:
            raise ValueError(f"No I/O lock for session: {session_id}")

        if not lock.acquire(timeout=timeout):
            return ReadResult(data="", bytes_read=0, timed_out=True)

        try:
            master_fd = session.master_fd
            if master_fd < 0:
                return ReadResult(data="", bytes_read=0, eof=True)

            deadline = time.monotonic() + timeout
            data_chunks: list[bytes] = []
            total_bytes = 0
            timed_out = False

            while total_bytes < max_bytes:
                remaining = max_bytes - total_bytes
                if remaining <= 0:
                    break
                timeout_left = max(0.1, deadline - time.monotonic())
                if timeout_left <= 0:
                    timed_out = True
                    break

                ready, _, _ = select.select([master_fd], [], [], timeout_left)
                if not ready:
                    timed_out = True
                    break

                try:
                    chunk = os.read(master_fd, remaining)
                    if not chunk:
                        # EOF
                        self._registry.heartbeat(session_id)
                        return ReadResult(
                            data=b"".join(data_chunks).decode("utf-8", errors="replace"),
                            bytes_read=total_bytes,
                            eof=True,
                        )
                    data_chunks.append(chunk)
                    total_bytes += len(chunk)
                except BlockingIOError:
                    # No data available right now, continue
                    continue

            result_data = b"".join(data_chunks).decode("utf-8", errors="replace")
            if total_bytes > 0:
                self._registry.heartbeat(session_id, bytes_read=total_bytes, last_output_at=time.monotonic())

            return ReadResult(data=result_data, bytes_read=total_bytes, timed_out=timed_out)
        finally:
            lock.release()

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        """Resize a shell session's terminal.

        Args:
            session_id: Session to resize.
            rows: New row count.
            cols: New column count.
        """
        session = self._registry.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        self._pty_manager.set_window_size(session.master_fd, rows, cols)
        self._registry.update_terminal_size(session_id, rows=rows, cols=cols)

        # Send SIGWINCH to process group to inform shell of resize
        if session.pid > 0:
            try:
                os.kill(session.pid, signal.SIGWINCH)
            except (ProcessLookupError, PermissionError):
                pass

    def close_session(self, session_id: str, reason: str = "explicit") -> None:
        """Close a shell session gracefully.

        Sends SIGHUP to the process group, waits for graceful shutdown,
        then cleans up PTY and registry entry.

        Args:
            session_id: Session to close.
            reason: Reason for closing (for logging).
        """
        session = self._registry.get(session_id)
        if session is None:
            return

        # Stop heartbeat thread
        stop_event = self._heartbeat_threads.pop(session_id, None)
        if stop_event:
            stop_event.set()

        # Update state
        self._registry.update_state(session_id, SessionState.STOPPING)

        # Send SIGHUP to process group
        if session.pid > 0:
            try:
                os.killpg(session.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass

            # Give it a moment to clean up
            try:
                os.waitpid(session.pid, os.WNOHANG)
            except ChildProcessError:
                pass

        # Close PTY master
        self._pty_manager.close(session.master_fd)

        # Remove I/O lock
        self._io_locks.pop(session_id, None)

        # Remove from registry
        self._registry.close_session(session_id, reason=reason)

    def _start_heartbeat(self, session_id: str) -> None:
        """Start the heartbeat thread for a session."""
        stop_event = threading.Event()
        self._heartbeat_threads[session_id] = stop_event

        def heartbeat_loop() -> None:
            while not stop_event.wait(self._heartbeat_interval):
                session = self._registry.get(session_id)
                if session is None or not session.is_alive:
                    break
                # Check if process is still alive
                if session.pid > 0:
                    try:
                        pgid = os.getpgid(session.pid)
                        alive = True
                    except ProcessLookupError:
                        alive = False

                    if not alive:
                        self._registry.update_state(session_id, SessionState.ZOMBIE)
                        break

                # Emit heartbeat
                self._registry.heartbeat(session_id)

        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()

    def get_session(self, session_id: str) -> ShellSession | None:
        """Get session metadata."""
        return self._registry.get(session_id)

    def list_active_sessions(self) -> list[ShellSession]:
        """List all active sessions."""
        return self._registry.list_active()

    def snapshot(self) -> dict[str, Any]:
        """Return full adapter snapshot."""
        return {
            "registry": self._registry.snapshot(),
            "pty_open_count": len(self._pty_manager.list_open()),
        }
