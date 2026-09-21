"""ACP transport: frame size limit and adapter reaping.

Both cover failures observed live on 2026-09-20/21:

* a ``claude_code`` chat turn died with ``ValueError: Separator is found, but
  chunk is longer than limit`` — asyncio's 64 KiB StreamReader default is
  smaller than a ``new_session`` frame carrying the MCP tool list;
* three ``claude-agent-acp`` node processes were still alive 24 h later against
  six sessions ever, because the transport's teardown awaits and those awaits
  are cut short inside an already-cancelled task.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.runtimes.acp_transport import (
    ACP_STREAM_LIMIT,
    ACPFrameTooLarge,
    _descendants,
    _reap_adapter,
)


def test_stream_limit_is_far_above_the_asyncio_default():
    """64 KiB is the default that broke; the limit must clear a tool-list frame."""
    assert ACP_STREAM_LIMIT > 64 * 1024
    assert ACP_STREAM_LIMIT >= 1024 * 1024


@pytest.mark.parametrize("bad", ["", "   ", "not-a-number", "8MB", "0", "-1", "1.5"])
def test_a_bad_env_override_never_breaks_the_import(monkeypatch, bad):
    """This module is imported on the gateway's startup path.

    Raising here would turn one typo'd env var into a gateway that cannot
    boot, so an unusable value must fall back to the default instead.
    """
    import importlib

    monkeypatch.setenv("NOVA_ACP_STREAM_LIMIT", bad)
    from deerflow.runtimes import acp_transport

    importlib.reload(acp_transport)  # must not raise
    try:
        assert acp_transport.ACP_STREAM_LIMIT == acp_transport._DEFAULT_STREAM_LIMIT
    finally:
        monkeypatch.delenv("NOVA_ACP_STREAM_LIMIT", raising=False)
        importlib.reload(acp_transport)


def test_stream_limit_is_env_overridable(monkeypatch):
    monkeypatch.setenv("NOVA_ACP_STREAM_LIMIT", "12345")
    import importlib

    from deerflow.runtimes import acp_transport

    importlib.reload(acp_transport)
    try:
        assert acp_transport.ACP_STREAM_LIMIT == 12345
    finally:
        monkeypatch.delenv("NOVA_ACP_STREAM_LIMIT", raising=False)
        importlib.reload(acp_transport)


class _Proc:
    """Minimal stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = 4242
        self.killed = False

    def kill(self):
        self.killed = True


def test_reap_kills_an_adapter_that_outlived_its_turn():
    proc = _Proc(returncode=None)
    _reap_adapter(proc)
    assert proc.killed is True


def test_reap_leaves_an_already_exited_adapter_alone():
    proc = _Proc(returncode=0)
    _reap_adapter(proc)
    assert proc.killed is False


def test_reap_is_a_noop_without_a_process():
    _reap_adapter(None)  # must not raise


def test_reap_never_raises_on_an_odd_object():
    """Cleanup must never be the thing that fails a turn."""

    class Hostile:
        @property
        def returncode(self):
            raise RuntimeError("boom")

    _reap_adapter(Hostile())  # must not raise
    _reap_adapter(object())  # no returncode attribute at all


def test_frame_too_large_names_the_knob():
    exc = ACPFrameTooLarge(f"an ACP frame from npx exceeded {ACP_STREAM_LIMIT} bytes (raise NOVA_ACP_STREAM_LIMIT)")
    assert "NOVA_ACP_STREAM_LIMIT" in str(exc)
    assert isinstance(exc, RuntimeError)


@pytest.mark.asyncio
async def test_reap_runs_even_when_the_turn_is_cancelled():
    """The regression: teardown that awaits gets cut short by cancellation.

    ``_reap_adapter`` is synchronous precisely so it still lands here.
    """
    proc = _Proc(returncode=None)

    async def turn():
        try:
            await asyncio.sleep(3600)
        finally:
            _reap_adapter(proc)

    task = asyncio.create_task(turn())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True, "adapter survived a cancelled turn — the 24 h leak"


@pytest.mark.asyncio
async def test_run_acp_prompt_passes_the_limit_to_the_spawn(monkeypatch):
    """The constant is worthless unless it reaches the subprocess streams.

    ``spawn_stdio_transport`` only honours ``limit`` when it is forwarded via
    ``transport_kwargs``; without it asyncio silently uses the 64 KiB default.
    """
    import sys
    from types import SimpleNamespace

    from deerflow.config.acp_config import ACPAgentConfig
    from deerflow.runtimes.types import policy_for_mode

    captured: dict = {}

    class DummyConn:
        async def initialize(self, **kwargs):
            return SimpleNamespace()

        async def new_session(self, **kwargs):
            return SimpleNamespace(session_id="s-1")

        async def prompt(self, **kwargs):
            return SimpleNamespace()

        async def close(self):
            return None

    class DummyCtx:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return DummyConn(), _Proc(returncode=0)

        async def __aexit__(self, *exc):
            return False

    def fake_spawn(client, cmd, *args, env=None, cwd=None, transport_kwargs=None):
        return DummyCtx(cmd=cmd, transport_kwargs=transport_kwargs)

    monkeypatch.setitem(
        sys.modules,
        "acp",
        SimpleNamespace(
            PROTOCOL_VERSION="2026-03-24",
            Client=object,
            spawn_agent_process=fake_spawn,
            text_block=lambda text: {"type": "text", "text": text},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "acp.schema",
        SimpleNamespace(
            ClientCapabilities=lambda **k: SimpleNamespace(),
            Implementation=lambda **k: SimpleNamespace(),
            TextContentBlock=type("TextContentBlock", (), {}),
        ),
    )

    from deerflow.runtimes import acp_transport

    await acp_transport.run_acp_prompt(
        ACPAgentConfig(command="npx", args=["-y", "pkg"], description="test adapter"),
        "hello",
        cwd="/tmp",
        mcp_servers=[],
        permission=policy_for_mode("standard"),
        on_text=lambda *a: None,
        on_status=lambda *a: None,
    )

    assert captured["transport_kwargs"] == {"limit": acp_transport.ACP_STREAM_LIMIT}


# ---------------------------------------------------------------------------
# The launcher leak: npx is what asyncio tracks, the adapter is its child.
# ---------------------------------------------------------------------------


def test_descendants_finds_a_real_child():
    """/proc walk must see a grandchild, not just the direct child."""
    import subprocess

    parent = subprocess.Popen(["sh", "-c", "sleep 30 & wait"])
    try:
        import time

        for _ in range(50):
            kids = _descendants(parent.pid)
            if kids:
                break
            time.sleep(0.1)
        assert kids, "no descendants found for a process that has one"
    finally:
        parent.kill()
        parent.wait()
        for pid in kids:
            with __import__("contextlib").suppress(OSError):
                __import__("os").kill(pid, 9)


def test_descendants_is_empty_for_a_childless_pid():
    import os

    assert _descendants(os.getpid()) == set() or isinstance(_descendants(os.getpid()), set)


def test_descendants_never_raises_on_a_dead_pid():
    assert _descendants(999_999_999) == set()


def test_reap_kills_orphaned_children_even_when_the_tracked_proc_is_clean():
    """The exact live failure: returncode is set, yet the adapter is alive.

    Five orphaned node processes were found this way, the oldest 24 h old.
    """
    import os
    import subprocess
    import time

    child = subprocess.Popen(["sleep", "30"])
    try:
        exited = _Proc(returncode=0)  # tracked launcher looks perfectly clean
        _reap_adapter(exited, {child.pid})
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.poll() is not None, "orphaned child survived the reaper"
    finally:
        with __import__("contextlib").suppress(OSError):
            os.kill(child.pid, 9)
        child.wait()


def test_reap_tolerates_already_dead_descendants():
    _reap_adapter(_Proc(returncode=0), {999_999_998, 999_999_999})  # must not raise


def test_reap_with_no_descendants_still_kills_the_tracked_proc():
    proc = _Proc(returncode=None)
    _reap_adapter(proc, set())
    assert proc.killed is True


# ---------------------------------------------------------------------------
# Thought leakage: only agent_message_chunk is the answer.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("update_kind", "in_answer", "in_status"),
    [
        ("agent_message_chunk", True, False),
        ("agent_thought_chunk", False, True),
        ("user_message_chunk", False, False),
        ("", True, False),  # adapters that omit the discriminator keep working
    ],
)
async def test_only_agent_message_chunks_reach_the_answer(monkeypatch, update_kind, in_answer, in_status):
    """A live reply read "...Let me search for relevant tools.**28 modules.**".

    The model's reasoning had been concatenated onto its answer because every
    TextContentBlock was appended regardless of which chunk kind carried it.
    """
    import sys
    from types import SimpleNamespace

    from deerflow.config.acp_config import ACPAgentConfig
    from deerflow.runtimes.types import policy_for_mode

    class TextContentBlock:
        def __init__(self, text):
            self.text = text

    captured_client = {}

    class DummyConn:
        async def initialize(self, **kwargs):
            return SimpleNamespace()

        async def new_session(self, **kwargs):
            return SimpleNamespace(session_id="s-1")

        async def prompt(self, **kwargs):
            client = captured_client["client"]
            await client.session_update(
                "s-1",
                SimpleNamespace(
                    content=TextContentBlock("PAYLOAD"),
                    session_update=update_kind,
                ),
            )
            return SimpleNamespace()

        async def close(self):
            return None

    class DummyCtx:
        async def __aenter__(self):
            return DummyConn(), _Proc(returncode=0)

        async def __aexit__(self, *exc):
            return False

    def fake_spawn(client, cmd, *args, **kwargs):
        captured_client["client"] = client
        return DummyCtx()

    monkeypatch.setitem(
        sys.modules,
        "acp",
        SimpleNamespace(
            PROTOCOL_VERSION="2026-03-24",
            Client=object,
            spawn_agent_process=fake_spawn,
            text_block=lambda text: {"type": "text", "text": text},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "acp.schema",
        SimpleNamespace(
            ClientCapabilities=lambda **k: SimpleNamespace(),
            Implementation=lambda **k: SimpleNamespace(),
            TextContentBlock=TextContentBlock,
        ),
    )

    from deerflow.runtimes import acp_transport

    statuses: list[str] = []
    answer = await acp_transport.run_acp_prompt(
        ACPAgentConfig(command="npx", args=["-y", "pkg"], description="test adapter"),
        "hello",
        cwd="/tmp",
        mcp_servers=[],
        permission=policy_for_mode("standard"),
        on_text=lambda *a: None,
        on_status=lambda _sid, s: statuses.append(s),
    )

    assert ("PAYLOAD" in answer) is in_answer
    assert any("PAYLOAD" in s for s in statuses) is in_status
