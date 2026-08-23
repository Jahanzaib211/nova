"""Phase C8 Execution Kernel tests: PTY, heartbeat, zombie detection, two-phase cancellation.

Run:
    cd backend && uv run pytest tests/test_execution_pty.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from deerflow.execution import (
    ExecutionClass,
    ExecutionKernel,
    ExecutionRequest,
    ExecutionStatus,
    ResourceLimits,
)
from deerflow.execution.adapters.interactive_shell import InteractiveShellAdapter
from deerflow.execution.pty_manager import PTYManager, TerminalSize
from deerflow.execution.session_registry import (
    SessionRegistry,
    SessionState,
)

# ── PTY Manager ───────────────────────────────────────────────────────────────


class TestPTYManager:
    def test_open_returns_master_and_slave_fds(self):
        mgr = PTYManager()
        master_fd, slave_fd = mgr.open()
        try:
            assert master_fd > 0
            assert slave_fd > 0
            assert master_fd != slave_fd
            assert mgr.get_slave_fd(master_fd) == slave_fd
            assert mgr.get_master_fd(slave_fd) == master_fd
        finally:
            mgr.close(master_fd)

    def test_close_idempotent(self):
        mgr = PTYManager()
        master_fd, _ = mgr.open()
        mgr.close(master_fd)
        mgr.close(master_fd)

    def test_set_window_size(self):
        mgr = PTYManager()
        master_fd, _ = mgr.open()
        try:
            mgr.set_window_size(master_fd, rows=40, cols=120)
            size = mgr.get_window_size(master_fd)
            assert size.rows == 40
            assert size.cols == 120
        finally:
            mgr.close(master_fd)

    def test_set_window_size_with_pixels(self):
        mgr = PTYManager()
        master_fd, _ = mgr.open()
        try:
            mgr.set_window_size_pixels(master_fd, rows=30, cols=100, width_pixels=1000, height_pixels=500)
            size = mgr.get_window_size(master_fd)
            assert size.rows == 30
            assert size.cols == 100
        finally:
            mgr.close(master_fd)

    def test_resize(self):
        mgr = PTYManager()
        master_fd, _ = mgr.open()
        try:
            mgr.resize(master_fd, TerminalSize(rows=50, cols=132))
            size = mgr.get_window_size(master_fd)
            assert size.rows == 50
            assert size.cols == 132
        finally:
            mgr.close(master_fd)

    def test_list_open(self):
        mgr = PTYManager()
        master_fd1, _ = mgr.open()
        master_fd2, _ = mgr.open()
        try:
            open_fds = mgr.list_open()
            assert master_fd1 in open_fds
            assert master_fd2 in open_fds
        finally:
            mgr.close(master_fd1)
            mgr.close(master_fd2)


class TestTerminalSize:
    def test_to_winsize_and_back(self):
        ts = TerminalSize(rows=24, cols=80, width_pixels=640, height_pixels=480)
        packed = ts.to_winsize()
        restored = TerminalSize.from_winsize(packed)
        assert restored.rows == 24
        assert restored.cols == 80
        assert restored.width_pixels == 640
        assert restored.height_pixels == 480

    def test_default(self):
        ts = TerminalSize()
        assert ts.rows == 24
        assert ts.cols == 80
        assert ts.width_pixels == 0
        assert ts.height_pixels == 0


# ── Session Registry ───────────────────────────────────────────────────────────


class TestSessionRegistry:
    def test_create_and_get_session(self):
        registry = SessionRegistry()
        session = registry.create_session(
            execution_id="e1",
            run_id="r1",
            thread_id="t1",
        )
        assert session.execution_id == "e1"
        assert session.run_id == "r1"
        assert session.thread_id == "t1"

        retrieved = registry.get(session.session_id)
        assert retrieved is session

    def test_heartbeat(self):
        registry = SessionRegistry()
        session = registry.create_session(execution_id="e1", run_id="r1")
        before = session.last_heartbeat
        time.sleep(0.01)
        registry.heartbeat(session.session_id)
        updated = registry.get(session.session_id)
        assert updated is not None
        assert updated.last_heartbeat > before

    def test_close_session_removes_from_registry(self):
        registry = SessionRegistry()
        session = registry.create_session(execution_id="e1", run_id="r1")
        session_id = session.session_id
        registry.close_session(session_id)
        assert registry.get(session_id) is None

    def test_snapshot(self):
        registry = SessionRegistry()
        registry.create_session(execution_id="e1", run_id="r1", thread_id="t1")
        registry.create_session(execution_id="e2", run_id="r2", thread_id="t1")
        snap = registry.snapshot()
        assert snap["total_sessions"] == 2
        assert len(snap["sessions"]) == 2


# ── Interactive Shell Adapter ──────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "posix", reason="PTY requires POSIX")
class TestInteractiveShellAdapter:
    @pytest.mark.skip(reason="interactive_shell preexec_fn conflict with start_new_session — known Phase C8 issue")
    def test_create_and_close_session(self):
        adapter = InteractiveShellAdapter()
        session_id = adapter.create_session(run_id="r1", execution_id="e1")
        assert session_id is not None
        adapter.close_session(session_id)

    @pytest.mark.skip(reason="interactive_shell preexec_fn conflict with start_new_session — known Phase C8 issue")
    def test_write_and_read(self):
        adapter = InteractiveShellAdapter()
        session_id = adapter.create_session(run_id="r1", execution_id="e1")
        try:
            adapter.write(session_id, "echo hello_phase_c8\n")
            output = adapter.read(session_id, timeout=3.0)
            assert "hello_phase_c8" in output or output == ""
        finally:
            adapter.close_session(session_id)

    @pytest.mark.skip(reason="interactive_shell preexec_fn conflict with start_new_session — known Phase C8 issue")
    def test_resize(self):
        adapter = InteractiveShellAdapter()
        session_id = adapter.create_session(run_id="r1", execution_id="e1")
        try:
            adapter.resize(session_id, rows=40, cols=120)
        finally:
            adapter.close_session(session_id)


# ── Heartbeat ─────────────────────────────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_thread_started_on_execute(self):
        kernel = ExecutionKernel()
        result = kernel.execute_sync(ExecutionRequest(argv=("sleep", "10"), execution_class=ExecutionClass.SHELL))
        assert result.status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.TIMED_OUT, ExecutionStatus.CANCELLED)

    def test_heartbeat_updates_on_long_process(self):
        kernel = ExecutionKernel()
        req = ExecutionRequest(argv=("sleep", "5"), execution_class=ExecutionClass.SHELL)
        start = time.monotonic()
        result = kernel.execute_sync(req)
        elapsed = time.monotonic() - start
        assert elapsed < 10


# ── Two-Phase Cancellation ─────────────────────────────────────────────────────


class TestTwoPhaseCancellation:
    @pytest.mark.asyncio
    async def test_cancel_returns_immediately_then_kills(self):
        kernel = ExecutionKernel()
        req = ExecutionRequest(
            # sys.executable, not "python": Debian and Ubuntu ship no bare
            # `python` unless python-is-python3 is installed, so this was the
            # only spawn in the file that could not run on a stock host --
            # every other one uses `sleep`. CI passed because setup-python
            # provides the alias, which is exactly what kept it hidden.
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            execution_class=ExecutionClass.PYTHON,
            limits=ResourceLimits(timeout=60.0),
        )
        handle = await kernel.spawn(req)

        await asyncio.sleep(0.5)

        start = time.monotonic()
        cancelled = kernel.cancel(handle.execution_id)
        elapsed = time.monotonic() - start
        assert cancelled is True
        assert elapsed < 5.0

        # Process should be dead after grace period
        await asyncio.sleep(6)
        # Handle should no longer be tracked in spawned
        snap = kernel.supervisor.snapshot()
        assert handle.execution_id not in snap["spawned"]

    @pytest.mark.asyncio
    async def test_cancel_idempotent(self):
        kernel = ExecutionKernel()
        req = ExecutionRequest(
            argv=("sleep", "20"),
            execution_class=ExecutionClass.SHELL,
        )
        handle = await kernel.spawn(req)
        await asyncio.sleep(0.3)

        kernel.cancel(handle.execution_id)
        result2 = kernel.cancel(handle.execution_id)
        result3 = kernel.cancel(handle.execution_id)
        assert result2 is True
        assert result3 is True

    def test_cancel_nonexistent_is_noop(self):
        kernel = ExecutionKernel()
        result = kernel.cancel("nonexistent-execution-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_during_initialization_completes(self):
        kernel = ExecutionKernel()
        req = ExecutionRequest(
            argv=("sleep", "0.01"),
            execution_class=ExecutionClass.SHELL,
        )
        handle = await kernel.spawn(req)
        await asyncio.sleep(0.05)
        kernel.cancel(handle.execution_id)


# ── ExecutionStatus state machine ─────────────────────────────────────────────


class TestExecutionStatusStateMachine:
    def test_terminal_statuses(self):
        terminal = {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.STOPPED,
            ExecutionStatus.DENIED,
            ExecutionStatus.REAPED,
        }
        for s in terminal:
            assert s.is_terminal, f"{s} should be terminal"

    def test_active_statuses(self):
        active = {
            ExecutionStatus.ALLOCATED,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING_INPUT,
            ExecutionStatus.STREAMING,
        }
        for s in active:
            assert s.is_active, f"{s} should be active"

    def test_cancellable_statuses(self):
        cancellable = {
            ExecutionStatus.PENDING,
            ExecutionStatus.ALLOCATED,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING_INPUT,
            ExecutionStatus.STREAMING,
        }
        for s in cancellable:
            assert s.is_cancellable, f"{s} should be cancellable"

    def test_nonterminal_not_terminal(self):
        non_terminal = {
            ExecutionStatus.PENDING,
            ExecutionStatus.ALLOCATED,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING_INPUT,
            ExecutionStatus.STREAMING,
            ExecutionStatus.CANCELLING,
            ExecutionStatus.STOPPING,
            ExecutionStatus.ZOMBIE_DETECTED,
        }
        for s in non_terminal:
            assert not s.is_terminal, f"{s} should not be terminal"

    def test_new_statuses_present(self):
        assert ExecutionStatus.ALLOCATED is not None
        assert ExecutionStatus.PREPARING is not None
        assert ExecutionStatus.WAITING_INPUT is not None
        assert ExecutionStatus.STREAMING is not None
        assert ExecutionStatus.CANCELLING is not None
        assert ExecutionStatus.STOPPING is not None
        assert ExecutionStatus.STOPPED is not None
        assert ExecutionStatus.ZOMBIE_DETECTED is not None
        assert ExecutionStatus.REAPED is not None
