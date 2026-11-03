"""The blocking AIO exec is bounded in time and observes the run's stop signal (R3-3).

Before this, ``AioSandbox._execute_command_raising`` made one blocking
``shell.exec_command`` call with no wall-clock limit and no cancellation
check: a hung command held its tool call -- and the whole run -- until the
2 h run deadline, and the user's Stop did nothing until it returned.

Pinned here:
* a command that outlives ``command_timeout`` returns an explicit ``Error:``
  string (never partial output that reads as success) and its in-sandbox
  process is killed via ``shell.kill_process`` on its own session;
* the server is asked to enforce the limit itself (``hard_timeout``), and a
  ``hard_timeout`` status from the server is reported as a timeout;
* setting the run's abort event ends a running command promptly, kills it,
  and releases the lock for the next command;
* the tool layer hands the abort event to the blocking path;
* the limit is configurable through ``sandbox.command_timeout``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nova.config.sandbox_config import SandboxConfig


@pytest.fixture()
def make_sandbox():
    def _make(**kwargs):
        with patch("nova.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
            from nova.community.aio_sandbox.aio_sandbox import AioSandbox

            sb = AioSandbox(id="sb-bounds", base_url="http://localhost:8080", **kwargs)
        sb._CANCEL_POLL_INTERVAL = 0.01
        sb._CLIENT_DEADLINE_GRACE = 0.0
        return sb

    return _make


class _HangingShell:
    """A shell whose exec blocks until kill_process is called on its session."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.killed: list[str] = []
        self.cleaned: list[str] = []
        self.exec_kwargs: list[dict] = []
        self._released = threading.Event()

    def create_session(self, id, **kwargs):  # noqa: A002 - SDK signature
        self.created.append(id)

    def exec_command(self, **kwargs):
        self.exec_kwargs.append(kwargs)
        self._released.wait(10)
        return SimpleNamespace(data=SimpleNamespace(output="", status="terminated"))

    def kill_process(self, *, id):  # noqa: A002 - SDK signature
        self.killed.append(id)
        self._released.set()

    def cleanup_session(self, session_id, **kwargs):
        self.cleaned.append(session_id)


def _run_in_thread(fn, *args, **kwargs):
    box: dict = {}

    def target():
        box["result"] = fn(*args, **kwargs)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, box


def test_default_timeout_is_well_under_the_run_deadline(make_sandbox):
    sb = make_sandbox()
    assert 0 < sb._command_timeout <= 1800
    assert SandboxConfig(use="x").command_timeout == 1800


def test_hung_command_times_out_and_is_killed(make_sandbox):
    sb = make_sandbox(command_timeout=0.2)
    shell = _HangingShell()
    sb._client.shell = shell

    started = time.monotonic()
    out = sb.execute_command("sleep 9999")
    elapsed = time.monotonic() - started

    assert elapsed < 5, "the command was not bounded"
    assert out.startswith("Error: command timed out")
    assert "did NOT finish" in out
    # The process ran on its own session, and that session was killed + released.
    assert shell.killed == shell.created and len(shell.created) == 1
    assert shell.cleaned == shell.created
    assert shell.exec_kwargs[0]["id"] == shell.created[0]
    # The server was asked to enforce the limit itself.
    assert 0 < shell.exec_kwargs[0]["hard_timeout"] <= 1.0
    # The lock is free for the next command.
    assert sb._lock.acquire(blocking=False)
    sb._lock.release()


def test_server_hard_timeout_status_is_reported_as_a_timeout(make_sandbox):
    sb = make_sandbox(command_timeout=60)
    sb._client.shell.exec_command.return_value = SimpleNamespace(data=SimpleNamespace(output="step 1\nstep 2", status="hard_timeout"))

    out = sb.execute_command("make build")

    assert out.startswith("Error: command timed out after 60s")
    assert "step 2" in out, "the output produced before the limit is kept, labelled"


def test_abort_event_cancels_a_running_command(make_sandbox):
    sb = make_sandbox(command_timeout=600)
    shell = _HangingShell()
    sb._client.shell = shell
    event = threading.Event()

    t, box = _run_in_thread(sb.execute_command, "npm run build", cancel_event=event)
    deadline = time.monotonic() + 5
    while not shell.exec_kwargs and time.monotonic() < deadline:
        time.sleep(0.01)
    event.set()
    t.join(5)

    assert not t.is_alive(), "the stop signal did not end the command"
    assert box["result"].startswith("Error: command cancelled by the run's stop signal")
    assert shell.killed == shell.created


def test_asyncio_abort_event_is_honoured(make_sandbox):
    sb = make_sandbox(command_timeout=600)
    shell = _HangingShell()
    sb._client.shell = shell
    event = asyncio.Event()
    event.set()

    out = sb.execute_command("sleep 9999", cancel_event=event)

    assert out.startswith("Error: command cancelled")


def test_a_normal_command_is_unaffected(make_sandbox):
    sb = make_sandbox(command_timeout=600)
    sb._client.shell.exec_command.return_value = SimpleNamespace(data=SimpleNamespace(output="hello", status="completed"))

    assert sb.execute_command("echo hello", cancel_event=threading.Event()) == "hello"
    sb._client.shell.kill_process.assert_not_called()
    sb._client.shell.cleanup_session.assert_called_once()


def test_zero_disables_the_limit_but_not_cancellation(make_sandbox):
    sb = make_sandbox(command_timeout=0)
    calls: list[dict] = []

    def exec_command(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(output="ok"))

    sb._client.shell.exec_command.side_effect = exec_command
    assert sb.execute_command("ls") == "ok"
    assert "hard_timeout" not in calls[0]


def test_tool_layer_passes_the_abort_event_to_the_blocking_path():
    from nova.sandbox import tools as sandbox_tools

    class Backend:
        supports_blocking_cancel = True

        def __init__(self):
            self.received = "not-called"

        def execute_command(self, command, cancel_event=None):
            self.received = cancel_event
            return "out"

    backend = Backend()
    event = asyncio.Event()
    assert sandbox_tools._execute_blocking(backend, "ls", event) == "out"
    assert backend.received is event


def test_tool_layer_leaves_backends_without_support_alone():
    from nova.sandbox import tools as sandbox_tools

    class Legacy:
        def execute_command(self, command):
            return "legacy"

    assert sandbox_tools._execute_blocking(Legacy(), "ls", asyncio.Event()) == "legacy"


def test_streaming_fallback_carries_the_abort_event():
    from nova.sandbox import tools as sandbox_tools

    class Backend:
        supports_streaming = True
        supports_blocking_cancel = True
        received = None

        def execute_command_streaming(self, command, on_chunk, cancel_event=None):
            raise RuntimeError("stream broke")

        def execute_command(self, command, cancel_event=None):
            Backend.received = cancel_event
            return "blocking"

    event = asyncio.Event()
    assert sandbox_tools._run_command_both_ways(Backend(), "ls", lambda t, r: None, cancel_event=event) == "blocking"
    assert Backend.received is event


def test_provider_passes_the_configured_timeout():
    from nova.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

    provider = AioSandboxProvider.__new__(AioSandboxProvider)
    provider._config = {"command_timeout": 120}
    assert provider._command_timeout() == 120.0
    provider._config = {}
    assert provider._command_timeout() is None
