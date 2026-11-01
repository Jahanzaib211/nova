"""A running command must observe the run's stop signal.

The 2026-11-01 incident had a run that could not be stopped for an hour: it
sat inside one streaming sandbox command, and the worker's stop check only
runs BETWEEN tool calls. The backend's streaming loop polled forever
(``while True``) with no way to be told the run was cancelled.

These tests pin the cancellation contract on both ends:
* the AIO streaming loop breaks out when the event is set, and says so in
  its output rather than returning partial output that reads as complete;
* the tool layer passes the run's abort event down to the backend;
* a backend that predates the parameter still works (TypeError fallback).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.community.aio_sandbox.aio_sandbox import _is_set  # noqa: E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# the cancellation probe
# ---------------------------------------------------------------------------


def test_is_set_handles_every_signal_shape() -> None:
    assert _is_set(None) is False

    event = threading.Event()
    assert _is_set(event) is False
    event.set()
    assert _is_set(event) is True

    assert _is_set(lambda: True) is True
    assert _is_set(lambda: False) is False


def test_is_set_never_raises_on_a_broken_signal() -> None:
    class Broken:
        def is_set(self):
            raise RuntimeError("boom")

    assert _is_set(Broken()) is False


def test_is_set_works_with_an_asyncio_event() -> None:
    event = asyncio.Event()
    assert _is_set(event) is False
    event.set()
    assert _is_set(event) is True


# ---------------------------------------------------------------------------
# the tool layer passes the abort event down
# ---------------------------------------------------------------------------


class _RecordingSandbox:
    """A streaming backend that records the cancel event it was handed."""

    supports_streaming = True

    def __init__(self) -> None:
        self.received = "not-called"

    def execute_command_streaming(self, command, on_chunk, cancel_event=None):
        self.received = cancel_event
        return "output"

    def execute_command(self, command) -> str:
        return "blocking-output"


async def test_tool_layer_passes_the_abort_event_to_the_backend() -> None:
    event = asyncio.Event()
    sandbox = _RecordingSandbox()
    sandbox_tools._run_command_both_ways(sandbox, "ls", lambda t, r: None, cancel_event=event)
    assert sandbox.received is event


async def test_backend_without_the_parameter_still_works() -> None:
    class OldBackend:
        supports_streaming = True

        def execute_command_streaming(self, command, on_chunk):
            return "old-style"

    out = sandbox_tools._run_command_both_ways(OldBackend(), "ls", lambda t, r: None, cancel_event=asyncio.Event())
    assert out == "old-style"


async def test_abort_event_is_read_from_the_runtime_context() -> None:
    event = asyncio.Event()

    class FakeRuntime:
        context = {"__run_abort_event": event, "thread_id": "t"}

    assert sandbox_tools._abort_event(FakeRuntime()) is event

    class EmptyRuntime:
        context = {"thread_id": "t"}

    assert sandbox_tools._abort_event(EmptyRuntime()) is None

    class NoContext:
        context = None

    assert sandbox_tools._abort_event(NoContext()) is None


# ---------------------------------------------------------------------------
# the backend's streaming loop honours the signal
# ---------------------------------------------------------------------------


async def test_streaming_loop_stops_when_cancelled() -> None:
    """The loop must break out of its poll and mark the output as cancelled."""
    from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

    sandbox = AioSandbox.__new__(AioSandbox)  # no client needed: we stub below
    sandbox._lock = threading.RLock()
    sandbox._busy_tracker = None

    started = threading.Event()

    def fake_create_session(id):  # noqa: A002 - ACP-ish signature
        return None

    def fake_exec_command(**kwargs):
        return None

    def fake_status(session_id):
        # Always "running": without a cancel check the loop would spin
        # forever, which is exactly the bug.
        return "running"

    def fake_read(log_path):
        return "line one\n"

    sandbox._require_client = lambda: type(
        "C",
        (),
        {
            "shell": type(
                "S",
                (),
                {
                    "create_session": staticmethod(fake_create_session),
                    "exec_command": staticmethod(fake_exec_command),
                    "cleanup_session": staticmethod(lambda *a, **k: None),
                },
            )
        },
    )()
    sandbox._stream_status = fake_status
    sandbox._read_stream_log = fake_read
    sandbox._cleanup_stream_session = lambda *a, **k: None

    event = threading.Event()
    event.set()  # already cancelled before the command starts

    out = sandbox.execute_command_streaming("sleep 999", lambda t, r: None, cancel_event=event)
    assert "cancelled" in out.lower()
    assert "line one" in out


async def test_streaming_loop_runs_to_completion_without_cancellation() -> None:
    from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

    sandbox = AioSandbox.__new__(AioSandbox)
    sandbox._lock = threading.RLock()
    sandbox._busy_tracker = None
    sandbox._require_client = lambda: type(
        "C",
        (),
        {
            "shell": type(
                "S",
                (),
                {
                    "create_session": staticmethod(lambda **k: None),
                    "exec_command": staticmethod(lambda **k: None),
                    "cleanup_session": staticmethod(lambda *a, **k: None),
                },
            )
        },
    )()
    calls = {"n": 0}

    def fake_status(session_id):
        calls["n"] += 1
        return "running" if calls["n"] < 2 else "completed"

    sandbox._stream_status = fake_status
    sandbox._read_stream_log = lambda p: "done\n"
    sandbox._cleanup_stream_session = lambda *a, **k: None

    out = sandbox.execute_command_streaming("echo hi", lambda t, r: None, cancel_event=threading.Event())
    assert "cancelled" not in out.lower()
    assert "done" in out
