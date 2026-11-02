"""Regression tests for the sandbox destroyed under a live run (run 3cc50519).

On 2026-11-01 one gateway process held three ``AioSandboxProvider`` instances
(three "Started idle checker thread" lines at 12:13:07). One created sandbox
``7ea3700c``; the other two only discovered it, so their activity clocks froze
at 12:13:11. At 13:14:07 both logged "idle for 3654s" and destroyed the sandbox
the lead agent had been using continuously since 13:05. The next ``bash``
failed to start streaming (``Connection refused``), fell back to blocking exec,
and the worker thread never returned: the streaming path holds the sandbox's
non-reentrant lock and the fallback takes it again.

Three defects, one test group each:

1. ``get_sandbox_provider()`` was an unlocked check-then-create.
2. Each instance judged idleness from its *own* activity clock, so an instance
   that never served a call reaped a sandbox another instance was serving.
3. The streaming fallback self-deadlocked, and a destroyed sandbox produced a
   hang instead of an error the agent could act on.
"""

from __future__ import annotations

import importlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

aio_mod = importlib.import_module("nova.community.aio_sandbox.aio_sandbox_provider")
provider_mod = importlib.import_module("nova.sandbox.sandbox_provider")

DEADLOCK_BUDGET_S = 5.0


@pytest.fixture(autouse=True)
def _clean_shared_activity():
    aio_mod._SHARED_ACTIVITY.clear()
    yield
    aio_mod._SHARED_ACTIVITY.clear()


def _bare_provider(idle_timeout: int = 600):
    """An AioSandboxProvider with real registries and lock but no __init__ side effects."""
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = threading.Lock()
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._first_seen = {}
    provider._busy = {}
    provider._warm_pool = {}
    provider._config = {"idle_timeout": idle_timeout}
    provider._backend = MagicMock()
    provider._thread_has_live_dev_server = lambda sandbox_id: False
    return provider


def _recording_destroy(provider) -> list[str]:
    destroyed: list[str] = []
    provider.destroy = destroyed.append
    return destroyed


# --- 1. one provider per process ----------------------------------------------


def test_concurrent_first_calls_build_exactly_one_provider(monkeypatch):
    built: list[object] = []

    class SlowProvider:
        def __init__(self, **kwargs):
            # Widen the race window the way a real __init__ (Docker discovery,
            # orphan reconciliation) does.
            time.sleep(0.05)
            built.append(self)

    monkeypatch.setattr(provider_mod, "get_app_config", lambda: SimpleNamespace(sandbox=SimpleNamespace(use="x:y")))
    monkeypatch.setattr(provider_mod, "resolve_class", lambda path, base: SlowProvider)
    provider_mod.reset_sandbox_provider()
    try:
        workers = 16
        barrier = threading.Barrier(workers)
        results: list[object] = []

        def call():
            barrier.wait()
            results.append(provider_mod.get_sandbox_provider())

        threads = [threading.Thread(target=call) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=DEADLOCK_BUDGET_S)

        assert len(built) == 1, f"{len(built)} provider instances were constructed"
        assert len(results) == workers
        assert all(r is built[0] for r in results)
    finally:
        provider_mod.reset_sandbox_provider()


def test_reset_still_yields_a_fresh_provider(monkeypatch):
    class P:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(provider_mod, "get_app_config", lambda: SimpleNamespace(sandbox=SimpleNamespace(use="x:y")))
    monkeypatch.setattr(provider_mod, "resolve_class", lambda path, base: P)
    provider_mod.reset_sandbox_provider()
    try:
        first = provider_mod.get_sandbox_provider()
        assert provider_mod.get_sandbox_provider() is first
        provider_mod.reset_sandbox_provider()
        assert provider_mod.get_sandbox_provider() is not first
    finally:
        provider_mod.reset_sandbox_provider()


def test_a_second_instance_in_one_process_is_logged(caplog):
    first, second = _bare_provider(), _bare_provider()
    aio_mod._register_live_instance(first)
    with caplog.at_level("WARNING"):
        aio_mod._register_live_instance(second)
    assert "more than one AioSandboxProvider" in caplog.text
    aio_mod._LIVE_INSTANCES.clear()


# --- 2. the reaper never destroys a sandbox someone else is serving --------------


def test_an_instance_that_only_discovered_a_sandbox_does_not_reap_it_while_another_serves_it():
    serving, discoverer = _bare_provider(), _bare_provider()
    sid = "7ea3700c"
    serving._sandboxes[sid] = MagicMock()
    serving._last_activity[sid] = time.time()
    # The discoverer saw the sandbox an hour ago and has served nothing since.
    discoverer._sandboxes[sid] = MagicMock()
    discoverer._last_activity[sid] = time.time() - 3654
    destroyed = _recording_destroy(discoverer)

    serving.get(sid)  # the lead agent's tool call, routed to the serving instance

    discoverer._cleanup_idle_sandboxes(600)
    assert destroyed == []


def test_a_command_in_flight_on_another_instance_protects_the_sandbox():
    serving, discoverer = _bare_provider(), _bare_provider()
    sid = "sb-busy"
    discoverer._last_activity[sid] = time.time() - 7200
    destroyed = _recording_destroy(discoverer)

    serving._track_busy(sid, +1)
    # Even with every idle clock far in the past, the running command wins.
    aio_mod._SHARED_ACTIVITY._last[sid] = time.time() - 7200

    discoverer._cleanup_idle_sandboxes(600)
    assert destroyed == []

    serving._track_busy(sid, -1)
    aio_mod._SHARED_ACTIVITY._last[sid] = time.time() - 7200
    discoverer._cleanup_idle_sandboxes(600)
    assert destroyed == [sid]


def test_a_warm_pool_entry_is_not_reaped_while_another_instance_serves_it():
    serving, holder = _bare_provider(), _bare_provider()
    sid = "sb-warm"
    holder._warm_pool[sid] = (SimpleNamespace(sandbox_id=sid), time.time() - 3654)
    serving._sandboxes[sid] = MagicMock()

    serving.get(sid)

    holder._cleanup_idle_sandboxes(600)
    assert sid in holder._warm_pool
    holder._backend.destroy.assert_not_called()


def test_a_genuinely_idle_sandbox_is_still_reaped():
    # Guard against the fix protecting everything forever.
    provider = _bare_provider()
    sid = "sb-idle"
    provider._last_activity[sid] = time.time() - 3654
    destroyed = _recording_destroy(provider)

    provider._cleanup_idle_sandboxes(600)
    assert destroyed == [sid]


def test_destroy_forgets_the_shared_activity():
    provider = _bare_provider()
    sid = "sb-gone"
    provider._sandboxes[sid] = MagicMock()
    provider.get(sid)
    assert aio_mod._SHARED_ACTIVITY.last(sid) is not None

    provider._remove_tracked_sandbox(sid)
    assert aio_mod._SHARED_ACTIVITY.last(sid) is None


# --- 3. a destroyed sandbox is an error, never a hang ---------------------------


@pytest.fixture()
def sandbox():
    with patch("nova.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from nova.community.aio_sandbox.aio_sandbox import AioSandbox

        sb = AioSandbox(id="7ea3700c", base_url="http://localhost:8080")
        sb._STREAM_POLL_INTERVAL = 0
        return sb


def _run_with_budget(fn, *args):
    """Run ``fn`` in a thread; fail the test (rather than hang the suite) on a deadlock."""
    box: dict[str, object] = {}

    def target():
        box["result"] = fn(*args)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=DEADLOCK_BUDGET_S)
    assert not t.is_alive(), f"{getattr(fn, '__name__', fn)} did not return within {DEADLOCK_BUDGET_S}s -- deadlock"
    return box["result"]


def test_a_streaming_start_failure_falls_back_without_deadlocking(sandbox):
    """The exact path of 13:14:51: streaming fails to start, the blocking exec takes over.

    Uses the real ``_execute_command_locked`` -- the existing fallback tests
    patch it out, which is why none of them could see the lock being taken twice.
    """
    sandbox._client.shell.create_session.side_effect = RuntimeError("no session")
    sandbox._client.shell.exec_command.return_value = SimpleNamespace(data=SimpleNamespace(output="42 styles.css"))

    out = _run_with_budget(sandbox.execute_command_streaming, "wc -l styles.css", lambda t, r: None)
    assert out == "42 styles.css"


def test_the_error_observation_backstop_does_not_deadlock(sandbox):
    from nova.community.aio_sandbox.aio_sandbox import _ERROR_OBSERVATION_SIGNATURE

    sandbox._client.file.read_file.return_value = SimpleNamespace(data=SimpleNamespace(content=f"Command failed: {_ERROR_OBSERVATION_SIGNATURE}"))
    sandbox._client.shell.view.return_value = SimpleNamespace(data=SimpleNamespace(status="completed"))
    sandbox._client.shell.exec_command.side_effect = [
        SimpleNamespace(data=None),  # the async streaming start
        SimpleNamespace(data=None),  # cleanup: rm of the stream log (best-effort)
        SimpleNamespace(data=SimpleNamespace(output="recovered")),  # the blocking retry
    ]

    assert _run_with_budget(sandbox.execute_command_streaming, "x", lambda t, r: None) == "recovered"


def test_an_unreachable_sandbox_fails_fast_with_an_actionable_error(sandbox):
    refused = ConnectionRefusedError(111, "Connection refused")
    sandbox._client.shell.create_session.side_effect = refused
    sandbox._client.shell.exec_command.side_effect = refused
    notified: list[str] = []
    sandbox._on_unreachable = notified.append

    out = _run_with_budget(sandbox.execute_command_streaming, "wc -l styles.css", lambda t, r: None)

    assert out.startswith("Error:")
    assert "unreachable" in out
    assert "retry" in out.lower()
    assert notified == ["7ea3700c"]


def test_an_ordinary_command_error_is_not_reported_as_unreachable(sandbox):
    sandbox._client.shell.exec_command.side_effect = ValueError("bad request")
    notified: list[str] = []
    sandbox._on_unreachable = notified.append

    out = _run_with_budget(sandbox.execute_command, "x")
    assert out == "Error: bad request"
    assert notified == []


def test_the_provider_drops_a_sandbox_the_backend_confirms_dead():
    provider = _bare_provider()
    sid = "7ea3700c"
    info = SimpleNamespace(sandbox_id=sid)
    sb = MagicMock()
    provider._sandboxes[sid] = sb
    provider._sandbox_infos[sid] = info
    provider._thread_sandboxes["thread-1"] = sid
    provider._backend.is_alive.return_value = False

    provider._on_sandbox_unreachable(sid)

    # The next tool call finds nothing and ensure_sandbox_initialized acquires afresh.
    assert provider.get(sid) is None
    assert "thread-1" not in provider._thread_sandboxes


@pytest.mark.parametrize("alive", [True, None])
def test_the_provider_keeps_a_sandbox_that_is_alive_or_unknown(alive):
    # A refused connection can be transient; only a definitive "dead" drops it.
    provider = _bare_provider()
    sid = "sb-flaky"
    provider._sandboxes[sid] = MagicMock()
    provider._sandbox_infos[sid] = SimpleNamespace(sandbox_id=sid)
    if alive is None:
        provider._backend.is_alive.side_effect = RuntimeError("docker unavailable")
    else:
        provider._backend.is_alive.return_value = alive

    provider._on_sandbox_unreachable(sid)
    assert provider.get(sid) is not None
