"""Regression tests for the dev-server readiness watchdog.

Pinned by the 2026-08-14 incident where ``start_dev_server`` returned a handle
in ``status: "starting"`` forever even though the child process had already
crashed (the panel's ``Compiling...`` spinner never resolved). The watchdog is
the contract that flips a stuck ``starting`` handle to ``crashed`` once the
candidate port has not become reachable within the readiness window.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

# The watchdog lives in the harness package; add it to sys.path if the test
# runner's cwd is backend/ (it usually is, but defensive).
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class _FakeSpawnedProcess:
    """Minimal stand-in for ``deerflow.execution.supervisor.SpawnedProcess``."""

    def __init__(self) -> None:
        self.returncode = None
        self.stdout = None


class _FakeKernel:
    """Records the spawn call; returns a process that never produces output."""

    last_request = None

    async def spawn(self, request, *, start_new_session: bool = False):
        type(self).last_request = request
        return _FakeSpawnedProcess()


class _FakeServiceContainer:
    def __init__(self, kernel: _FakeKernel) -> None:
        self._kernel = kernel

    def execution_kernel(self) -> _FakeKernel:
        return self._kernel


@contextmanager
def _patched_kernel(kernel: _FakeKernel):
    """Install a fake kernel + fake service_container.execution_kernel()."""

    import deerflow.execution as execution_pkg
    from deerflow.services import container as container_pkg

    real_kernel = getattr(execution_pkg, "ExecutionKernel", None)
    real_container = container_pkg.service_container

    class _DummyExecutionKernel:
        pass

    container_pkg.service_container = _FakeServiceContainer(kernel)
    # The spawn code does ``from deerflow.execution import ExecutionClass, ExecutionRequest, ResourceLimits``
    # and ``from deerflow.services.container import service_container`` lazily; the patch above is enough.

    try:
        yield
    finally:
        container_pkg.service_container = real_container


def _free_port() -> int:
    """Find a TCP port nothing is listening on right now."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _restore_port_alive():
    """Save and restore ``dev_server.port_alive`` so monkey-patching in one test
    cannot bleed into another (port_alive is a module-level attribute)."""
    import deerflow.sandbox.dev_server as ds

    original = ds.port_alive
    yield
    ds.port_alive = original


@pytest.mark.asyncio
async def test_local_handle_flips_to_crashed_when_nothing_binds_the_port(tmp_path):
    """A command that never binds a port (here: a hand-rolled handle whose
    host:port nobody listens on) must flip the handle from ``starting`` to
    ``crashed`` within the readiness window so the panel can render the red
    ``Dev server crashed`` pill instead of an endless spinner."""

    import deerflow.sandbox.dev_server as ds

    # Patch the watchdog timeout down to ~0.6 s so the test stays fast.
    ds._READINESS_TIMEOUT_S = 0.6

    # Force the liveness probe to always report "not listening" so we don't
    # depend on which ephemeral port the kernel happens to give us in this
    # test environment (it may already be bound by a leftover dev server).
    async def _never_alive(host: str, port: int, timeout: float = 0.4) -> bool:
        return False

    ds.port_alive = _never_alive  # type: ignore[assignment]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    handle = ds.DevServerHandle(
        thread_id="t-watchdog",
        port=free_port,
        cwd=str(tmp_path),
        command="sleep 60",  # would bind nothing
        label="app",
        host="127.0.0.1",
        _order=0,
    )
    ds._servers[ds._server_key(handle.thread_id, handle.label)] = handle

    # Drive the watchdog directly — no background task scheduling — so the
    # assertion can observe the state change before the test returns.
    await ds._watch_dev_server_start(handle)

    assert handle.status == ds._STATUS_CRASHED, (
        f"expected crashed within the readiness window, got {handle.status!r}"
    )
    assert any("crashed" in line for line in handle.log_buffer), (
        f"handle.log_buffer should record the crash reason; got {list(handle.log_buffer)!r}"
    )

    await ds.stop_dev_server(handle.thread_id, handle.label)


@pytest.mark.asyncio
async def test_local_handle_flips_to_ready_when_port_binds(tmp_path):
    """Smoke-test the happy path: a real TCP listener on the handle's port
    causes the watchdog to mark the handle ``ready`` quickly."""

    import deerflow.sandbox.dev_server as ds

    ds._READINESS_TIMEOUT_S = 5.0

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen()

    handle = ds.DevServerHandle(
        thread_id="t-ready",
        port=port,
        cwd=str(tmp_path),
        command="sleep 60",
        label="app",
        host="127.0.0.1",
        _order=0,
    )
    ds._servers[ds._server_key(handle.thread_id, handle.label)] = handle

    try:
        await ds._watch_dev_server_start(handle)
        assert handle.status == "ready", f"expected ready, got {handle.status!r}"
    finally:
        listener.close()
        await ds.stop_dev_server("t-ready", "app")


@pytest.mark.asyncio
async def test_stop_dev_server_cancels_watchdog(tmp_path):
    """``stop_dev_server`` must cancel the in-flight watchdog so it doesn't
    resurrect the handle after we deleted it."""

    import deerflow.sandbox.dev_server as ds

    ds._READINESS_TIMEOUT_S = 10.0  # long; we cancel before it fires

    kernel = _FakeKernel()
    with _patched_kernel(kernel):
        handle = await ds._start_dev_server_local(
            thread_id="t-cancel",
            cwd=str(tmp_path),
            command="sleep 60",
            label="app",
        )
    assert handle._watchdog_task is not None

    await ds.stop_dev_server("t-cancel", "app")
    assert handle._watchdog_task.cancelled() or handle._watchdog_task.done()