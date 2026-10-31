"""A dead ACP adapter must fail the turn in seconds, not after the turn budget.

The ``acp`` library's receive loop stops on EOF without failing the pending
request, so ``initialize`` waited for the outer timeout — 30 minutes in
production (2026-10-29) for an adapter that died 8 seconds in with
``claude-agent-acp: not found``. The transport now races each request against
the process exiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.acp_transport import ACPAdapterExited, run_acp_prompt
from deerflow.runtimes.types import policy_for_mode


def _run(cfg: ACPAgentConfig, tmp_path) -> None:
    asyncio.run(
        run_acp_prompt(
            cfg,
            "hello",
            cwd=str(tmp_path),
            mcp_servers=[],
            permission=policy_for_mode("plan"),
            on_text=lambda _sid, _t: None,
            on_status=lambda _sid, _t: None,
            timeout=60,
        )
    )


def test_adapter_that_exits_immediately_fails_fast_with_its_stderr(tmp_path):
    cfg = ACPAgentConfig(command="sh", args=["-c", "echo 'claude-agent-acp: not found' >&2; exit 127"], description="dead")
    started = time.monotonic()
    with pytest.raises(ACPAdapterExited) as exc:
        _run(cfg, tmp_path)
    assert time.monotonic() - started < 10, "must not wait for the turn budget"
    assert "127" in str(exc.value)
    assert "not found" in str(exc.value)


def _fake_adapter(monkeypatch, *, cleanup_raises: bool) -> None:
    """Install a fake ACP adapter that dies on the first request.

    ``cleanup_raises`` reproduces what the ACP library does when the adapter
    is already gone: closing its connection raises the same broken-pipe
    error, and that error used to replace the real diagnosis.
    """
    import acp

    class _FakeProc:
        pid = 999_999
        returncode = 127
        stderr = None

        async def wait(self):
            await asyncio.sleep(0.05)
            return 127

        def kill(self):
            pass

    class _FakeConn:
        async def initialize(self, **kwargs):
            raise ConnectionResetError("Connection lost")

    class _Spawn:
        async def __aenter__(self):
            return _FakeConn(), _FakeProc()

        async def __aexit__(self, exc_type, exc, tb):
            if cleanup_raises:
                raise ConnectionResetError("Connection lost")
            return False

    monkeypatch.setattr(acp, "spawn_agent_process", lambda *a, **k: _Spawn())


def test_a_broken_pipe_reports_the_exit_not_a_connection_error(monkeypatch, tmp_path):
    """The adapter's pipe breaks *before* the exit is noticed.

    The request then fails with ECONNRESET/EPIPE, and that error used to win
    the race against the process exit: the turn failed with "Connection lost"
    — no exit code, no stderr, nothing for the operator to act on. It must be
    reported as the adapter exiting, with its code and its last words.

    The race is timing-dependent, so it is reproduced deterministically here:
    the request fails first, the process reaps a tick later.
    """
    _fake_adapter(monkeypatch, cleanup_raises=False)
    cfg = ACPAgentConfig(command="claude-agent-acp", description="dead")
    with pytest.raises(ACPAdapterExited) as exc:
        _run(cfg, tmp_path)
    assert "127" in str(exc.value)


def test_the_adapters_cleanup_does_not_mask_the_exit(monkeypatch, tmp_path):
    """A dead adapter breaks the library's own cleanup, and that must not win.

    Closing the connection to a process that is already gone raises the same
    broken-pipe error. Python lets an exception raised while leaving a
    ``with`` block replace the one the block raised — so the transport's
    diagnosis (exit code, stderr) was being thrown away for "Connection
    lost", and the operator was left with nothing.
    """
    _fake_adapter(monkeypatch, cleanup_raises=True)
    cfg = ACPAgentConfig(command="claude-agent-acp", description="dead")
    with pytest.raises(ACPAdapterExited) as exc:
        _run(cfg, tmp_path)
    assert "127" in str(exc.value)
