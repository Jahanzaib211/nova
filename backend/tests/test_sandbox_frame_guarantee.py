"""Frame guarantee for streamed terminal observations.

A streamed command writes an opening ``state:"running"`` frame and a closing
``state:"done"`` frame sharing one ``id``. When the sandbox's client was
released to the warm pool mid-run, both the streaming *and* the blocking
fallback raised — the tool returned an error string, but **no closing frame
was ever written**, so the Terminal tab rendered that command as perpetually
"running" (observed live on thread d142fdff: id 57c22701d5f1, spinner forever).

Pinned here:

1. every failure path still closes the frame before propagating;
2. a released-but-pooled sandbox is readopted through the provider exactly
   once and the command retried on the fresh handle;
3. a stale open id in the log is finalized before any new command opens one,
   so the log invariant "at most one open id" holds across crashes.
"""

from __future__ import annotations

import json
import threading

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools`
# import each other; entering from the workspace_tools side is the order the
# application itself uses (see test_bash_streaming.py for the same note).
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip
from deerflow.sandbox.exceptions import SandboxError  # noqa: E402


class _FakeLog:
    """Routes observation writes at a tmp file, bypassing thread-dir resolution."""

    def __init__(self, path):
        self.path = path

    def lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out


@pytest.fixture
def log(tmp_path, monkeypatch) -> _FakeLog:
    fake = _FakeLog(tmp_path / "sandbox.log")
    # The writer resolves sandbox->thread first, then the log path; pin both.
    monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: "t1")
    monkeypatch.setattr(sandbox_tools, "_sandbox_log_file", lambda tid: fake.path)
    return fake


class _ReleasedSandbox:
    """Streaming and blocking both fail the way a released warm-pool client does."""

    id = "a1646fa2"
    supports_streaming = True

    def execute_command_streaming(self, command, on_chunk):
        raise SandboxError(f"sandbox {self.id} has been released; its client is closed")

    def execute_command(self, command):
        raise SandboxError(f"sandbox {self.id} has been released; its client is closed")


class _HealthySandbox:
    id = "a1646fa2"
    supports_streaming = True

    def execute_command_streaming(self, command, on_chunk):
        on_chunk("ok\n", False)
        return "ok"

    def execute_command(self, command):
        return "ok"


class TestClosingFrameGuarantee:
    def test_double_failure_still_closes_the_frame(self, log, monkeypatch) -> None:
        """The exact production failure: released client, no readopt possible.

        The exception must propagate (the tool layer turns it into an error
        string for the agent) but ONLY after the closing frame exists."""
        monkeypatch.setattr(sandbox_tools, "_readopt_released", lambda sbx: None)

        with pytest.raises(SandboxError):
            sandbox_tools._stream_bash_observations(_ReleasedSandbox(), "a1646fa2", "echo hi")

        frames = [e for e in log.lines() if e.get("id")]
        assert frames, "no framed events were written"
        assert frames[0]["state"] == "running"
        assert frames[-1]["state"] == "done", f"dangling spinner: {frames[-1]}"

    def test_readopt_retries_once_on_a_fresh_handle(self, log, monkeypatch) -> None:
        fresh = _HealthySandbox()
        monkeypatch.setattr(sandbox_tools, "_readopt_released", lambda sbx: fresh)

        output = sandbox_tools._stream_bash_observations(_ReleasedSandbox(), "a1646fa2", "echo hi")

        assert output == "ok"
        frames = [e for e in log.lines() if e.get("id")]
        assert frames[-1]["state"] == "done"
        assert frames[-1]["output"] == "ok"


class TestOpenIdInvariant:
    def test_stale_open_id_is_finalized_before_the_next_command(self, log) -> None:
        """Simulate yesterday's crash residue, then run a healthy command."""
        stale = {"ts": "00:00:00", "type": "bash", "path": None, "summary": "$ old", "id": "dead1", "state": "running"}
        log.path.write_text(json.dumps(stale) + "\n")

        sandbox_tools._stream_bash_observations(_HealthySandbox(), "a1646fa2", "echo hi")

        entries = log.lines()
        finalized = [e for e in entries if e.get("id") == "dead1"]
        assert finalized[-1]["state"] == "done", "stale id never closed"
        # The synthetic close must land BEFORE the new command opens its own id.
        new_open_idx = next(i for i, e in enumerate(entries) if e.get("state") == "running" and e.get("id") != "dead1")
        dead_done_idx = entries.index(finalized[-1])
        assert dead_done_idx < new_open_idx

    def test_finalize_is_a_noop_with_no_dangling_ids(self, log) -> None:
        done = {"ts": "00:00:00", "type": "bash", "path": None, "summary": "$ x", "id": "done1", "state": "done"}
        log.path.write_text(json.dumps(done) + "\n")
        sandbox_tools._finalize_stale_open_ids("a1646fa2")
        assert len(log.lines()) == 1  # nothing appended

    def test_living_shell_sessions_are_never_finalized(self, log) -> None:
        """``shell:<session>`` ids stay running while the interactive process
        lives — closing them on every bash command would erase a live REPL
        from the Terminal mid-session."""
        live = {"ts": "00:00:00", "type": "shell_session", "path": None, "summary": "[main] started", "id": "shell:main", "state": "running"}
        log.path.write_text(json.dumps(live) + "\n")

        sandbox_tools._finalize_stale_open_ids("a1646fa2")

        assert len(log.lines()) == 1, "a live shell session was swept"


class TestReadoptReleasedProvider:
    """The provider must hand back a LIVE replacement only for sandboxes it
    still holds in its warm pool — silent resurrection of destroyed containers
    would be worse than the error."""

    def _provider(self):
        from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

        provider = object.__new__(AioSandboxProvider)
        provider._lock = threading.Lock()
        provider._warm_pool = {}
        provider._sandboxes = {}
        provider._sandbox_infos = {}
        provider._last_activity = {}
        provider._first_seen = {}
        provider._thread_sandboxes = {}
        provider._track_busy = None
        return provider

    def test_returns_none_for_an_active_sandbox(self) -> None:
        provider = self._provider()

        class _Open:
            id = "s1"
            closed = False

        assert provider.readopt_released(_Open()) is None

    def test_returns_none_when_not_pooled(self) -> None:
        provider = self._provider()

        class _Closed:
            id = "gone"
            closed = True

        assert provider.readopt_released(_Closed()) is None

    def test_readopted_sandbox_is_registered_and_returned(self, monkeypatch) -> None:
        provider = self._provider()
        info = type("Info", (), {"sandbox_url": "http://host:8183", "sandbox_id": "s9"})()
        provider._warm_pool["s9"] = (info, 0.0)

        class _Closed:
            id = "s9"
            closed = True

        monkeypatch.setattr(provider, "_check_tracked_sandbox_alive", lambda sid, i: True)

        fresh = provider.readopt_released(_Closed())
        assert fresh is not None
        assert fresh.id == "s9"
        assert provider._sandboxes["s9"] is fresh
        assert "s9" not in provider._warm_pool
