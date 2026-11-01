"""Review fixes for the AIO streaming fallback and provider bookkeeping.

* A refused streaming start falls back to ONE blocking exec -- the command
  used to run twice whenever the fallback succeeded.
* "Unreachable" is decided by the transport exception, never by the
  command's own output: ``curl`` to a dead port prints "Connection refused"
  and is a perfectly healthy sandbox.
* The blocking fallback runs with the sandbox lock released by the ``with``
  block itself, not by a manual release/re-acquire inside it.
* A cancelled streaming command is killed, not just abandoned.
* ``_on_sandbox_unreachable`` never drops a sandbox re-created under the same
  deterministic id, and closes the client it drops.
* The live-instance registry does not pin providers, and ``track_busy`` does
  not resurrect a forgotten sandbox.
"""

from __future__ import annotations

import gc
import importlib
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")

DEADLOCK_BUDGET_S = 5.0


@pytest.fixture()
def sandbox():
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        sb = AioSandbox(id="7ea3700c", base_url="http://localhost:8080")
        sb._STREAM_POLL_INTERVAL = 0
        return sb


def _run_with_budget(fn, *args, **kwargs):
    box: dict[str, object] = {}

    def target():
        box["result"] = fn(*args, **kwargs)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=DEADLOCK_BUDGET_S)
    assert not t.is_alive(), f"{getattr(fn, '__name__', fn)} did not return within {DEADLOCK_BUDGET_S}s -- deadlock"
    return box["result"]


# --- the refused-start fallback ------------------------------------------------


def test_a_refused_start_whose_fallback_succeeds_runs_the_command_once(sandbox):
    sandbox._client.shell.create_session.side_effect = ConnectionRefusedError(111, "Connection refused")
    sandbox._client.shell.exec_command.return_value = SimpleNamespace(data=SimpleNamespace(output="migrated 3 rows"))

    out = _run_with_budget(sandbox.execute_command_streaming, "./migrate.sh", lambda t, r: None)

    assert out == "migrated 3 rows"
    assert sandbox._client.shell.exec_command.call_count == 1


def test_command_output_mentioning_refused_is_not_unreachable(sandbox):
    curl_output = "curl: (7) Failed to connect to localhost port 9999: Connection refused"
    sandbox._client.shell.create_session.side_effect = ConnectionRefusedError(111, "Connection refused")
    sandbox._client.shell.exec_command.return_value = SimpleNamespace(data=SimpleNamespace(output=f"Error: {curl_output}"))
    notified: list[str] = []
    sandbox._on_unreachable = notified.append

    out = _run_with_budget(sandbox.execute_command_streaming, "curl localhost:9999", lambda t, r: None)

    assert curl_output in out
    assert "unreachable" not in out
    assert notified == []


@pytest.mark.parametrize(
    "transport_error",
    [
        ConnectionRefusedError(111, "Connection refused"),
        httpx.ConnectError("[Errno 111] Connection refused"),
        OSError(111, "Connection refused"),
    ],
)
def test_a_transport_error_from_the_fallback_is_unreachable(sandbox, transport_error):
    sandbox._client.shell.create_session.side_effect = transport_error
    sandbox._client.shell.exec_command.side_effect = transport_error
    notified: list[str] = []
    sandbox._on_unreachable = notified.append

    out = _run_with_budget(sandbox.execute_command_streaming, "ls", lambda t, r: None)

    assert out.startswith("Error:")
    assert "unreachable" in out
    assert notified == ["7ea3700c"]


def test_a_non_transport_fallback_error_is_reported_plainly(sandbox):
    sandbox._client.shell.create_session.side_effect = RuntimeError("no session")
    sandbox._client.shell.exec_command.side_effect = ValueError("bad request")
    notified: list[str] = []
    sandbox._on_unreachable = notified.append

    out = _run_with_budget(sandbox.execute_command_streaming, "ls", lambda t, r: None)

    assert out == "Error: bad request"
    assert notified == []


def test_the_fallback_runs_without_holding_the_lock(sandbox):
    """The blocking fallback must see the lock free, and leave it free."""
    sandbox._client.shell.create_session.side_effect = RuntimeError("no session")
    seen: list[bool] = []

    def exec_command(**kwargs):
        # _execute_command_locked holds the lock while it runs; what matters
        # is that the streaming path is not ALSO holding it (non-reentrant).
        seen.append(True)
        return SimpleNamespace(data=SimpleNamespace(output="ok"))

    sandbox._client.shell.exec_command.side_effect = exec_command

    assert _run_with_budget(sandbox.execute_command_streaming, "ls", lambda t, r: None) == "ok"
    assert seen == [True]
    assert sandbox._lock.acquire(blocking=False)
    sandbox._lock.release()


# --- cancellation kills the command ---------------------------------------------


def test_cancel_kills_the_streaming_process(sandbox):
    sandbox._client.shell.view.return_value = SimpleNamespace(data=SimpleNamespace(status="running"))
    sandbox._client.file.read_file.return_value = SimpleNamespace(data=SimpleNamespace(content="building...\n"))
    event = threading.Event()
    event.set()

    out = _run_with_budget(sandbox.execute_command_streaming, "make build", lambda t, r: None, cancel_event=event)

    assert "cancelled" in out
    sandbox._client.shell.kill_process.assert_called_once()
    session_id = sandbox._client.shell.kill_process.call_args.kwargs["id"]
    assert session_id.startswith("nova-stream-")
    sandbox._client.shell.cleanup_session.assert_called_once_with(session_id)


def test_a_failed_kill_never_raises_and_skips_the_rm_on_the_busy_session(sandbox):
    sandbox._client.shell.view.return_value = SimpleNamespace(data=SimpleNamespace(status="running"))
    sandbox._client.file.read_file.return_value = SimpleNamespace(data=SimpleNamespace(content="x\n"))
    sandbox._client.shell.kill_process.side_effect = RuntimeError("kill failed")
    event = threading.Event()
    event.set()

    out = _run_with_budget(sandbox.execute_command_streaming, "make build", lambda t, r: None, cancel_event=event)

    assert "cancelled" in out
    # Only the async start went through exec_command: no `rm -f` was queued
    # behind the still-running command.
    assert sandbox._client.shell.exec_command.call_count == 1
    sandbox._client.shell.cleanup_session.assert_called_once()


# --- provider bookkeeping ---------------------------------------------------------


def _bare_provider():
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = threading.Lock()
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._first_seen = {}
    provider._busy = {}
    provider._warm_pool = {}
    provider._config = {"idle_timeout": 600}
    provider._backend = MagicMock()
    return provider


def test_unreachable_does_not_drop_a_sandbox_recreated_meanwhile():
    provider = _bare_provider()
    sid = "7ea3700c"
    stale_info = SimpleNamespace(sandbox_id=sid)
    provider._sandboxes[sid] = MagicMock()
    provider._sandbox_infos[sid] = stale_info
    fresh_sandbox = MagicMock()
    fresh_info = SimpleNamespace(sandbox_id=sid)

    def is_alive(info):
        # Another thread re-creates the same deterministic id while the
        # health check is in flight.
        with provider._lock:
            provider._sandboxes[sid] = fresh_sandbox
            provider._sandbox_infos[sid] = fresh_info
        return False

    provider._backend.is_alive.side_effect = is_alive

    provider._on_sandbox_unreachable(sid)

    assert provider._sandboxes.get(sid) is fresh_sandbox
    assert provider._sandbox_infos.get(sid) is fresh_info
    fresh_sandbox.close.assert_not_called()


def test_unreachable_closes_the_dropped_client():
    provider = _bare_provider()
    sid = "sb-dead"
    sb = MagicMock()
    provider._sandboxes[sid] = sb
    provider._sandbox_infos[sid] = SimpleNamespace(sandbox_id=sid)
    provider._backend.is_alive.return_value = False

    provider._on_sandbox_unreachable(sid)

    assert sid not in provider._sandboxes
    sb.close.assert_called_once()


def test_a_failing_close_on_unreachable_is_not_fatal():
    provider = _bare_provider()
    sid = "sb-dead"
    sb = MagicMock()
    sb.close.side_effect = RuntimeError("socket already gone")
    provider._sandboxes[sid] = sb
    provider._sandbox_infos[sid] = SimpleNamespace(sandbox_id=sid)
    provider._backend.is_alive.return_value = False

    provider._on_sandbox_unreachable(sid)  # must not raise
    assert sid not in provider._sandboxes


def test_the_live_instance_registry_does_not_pin_providers(caplog):
    aio_mod._LIVE_INSTANCES.clear()
    first = _bare_provider()
    aio_mod._register_live_instance(first)
    del first
    gc.collect()
    assert len(aio_mod._LIVE_INSTANCES) == 0

    second = _bare_provider()
    with caplog.at_level("WARNING"):
        aio_mod._register_live_instance(second)
    assert "more than one AioSandboxProvider" not in caplog.text
    aio_mod._LIVE_INSTANCES.clear()


def test_shutdown_unregisters_the_instance():
    aio_mod._LIVE_INSTANCES.clear()
    provider = _bare_provider()
    provider._shutdown_called = False
    provider._idle_checker_stop = threading.Event()
    provider._idle_checker_thread = None
    aio_mod._register_live_instance(provider)

    provider.shutdown()

    assert provider not in aio_mod._LIVE_INSTANCES


def test_track_busy_does_not_resurrect_a_forgotten_sandbox():
    activity = aio_mod._SharedActivity()
    activity.track_busy("sb-x", +1)
    activity.forget("sb-x")  # destroyed mid-command

    activity.track_busy("sb-x", -1)  # the command's finally block

    assert activity.last("sb-x") is None
    assert activity.busy("sb-x") == 0


def test_track_busy_still_refreshes_a_tracked_sandbox_on_finish():
    activity = aio_mod._SharedActivity()
    activity.track_busy("sb-y", +1)
    activity._last["sb-y"] = 0.0

    activity.track_busy("sb-y", -1)

    assert activity.last("sb-y") > 0.0
    assert activity.busy("sb-y") == 0
