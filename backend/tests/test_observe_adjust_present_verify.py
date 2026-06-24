"""Tests for the deterministic auto-verify-on-present_files gate in ObserveAdjustMiddleware."""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares import observe_adjust_middleware as oam
from deerflow.agents.middlewares.observe_adjust_middleware import ObserveAdjustMiddleware


def _state(artifacts=None, sandbox_id="aio-thread-xyz"):
    return {
        "sandbox": {"sandbox_id": sandbox_id},
        "artifacts": artifacts if artifacts is not None else ["/mnt/user-data/outputs/index.html"],
    }


def _config(thread_id="thread-xyz"):
    return {"configurable": {"thread_id": thread_id}}


def _present_msgs():
    return [AIMessage(content="done"), ToolMessage("Successfully presented files", tool_call_id="t1", name="present_files")]


def _patch_create_task(monkeypatch):
    fired: list[tuple[str, str]] = []

    def fake_create_task(coro):
        # Pull args off the coroutine for assertions, then close it (no loop needed).
        try:
            fired.append((coro.cr_frame.f_locals.get("thread_id"), coro.cr_frame.f_locals.get("sandbox_id")))
        finally:
            coro.close()

        class _Dummy:
            pass

        return _Dummy()

    monkeypatch.setattr(oam.asyncio, "create_task", fake_create_task)
    return fired


def test_present_files_fires_verify_once(monkeypatch):
    oam._verified_present.clear()
    fired = _patch_create_task(monkeypatch)
    mw = ObserveAdjustMiddleware()

    mw._maybe_verify_present_files(_state(), _config(), _present_msgs())
    assert fired == [("thread-xyz", "aio-thread-xyz")]

    # Same deliverable set → deduped, does not fire again.
    mw._maybe_verify_present_files(_state(), _config(), _present_msgs())
    assert len(fired) == 1


def test_present_files_refires_on_new_artifacts(monkeypatch):
    oam._verified_present.clear()
    fired = _patch_create_task(monkeypatch)
    mw = ObserveAdjustMiddleware()

    mw._maybe_verify_present_files(_state(["/mnt/user-data/outputs/a.html"]), _config(), _present_msgs())
    mw._maybe_verify_present_files(_state(["/mnt/user-data/outputs/b.html"]), _config(), _present_msgs())
    assert len(fired) == 2


def test_local_sandbox_skipped(monkeypatch):
    oam._verified_present.clear()
    fired = _patch_create_task(monkeypatch)
    mw = ObserveAdjustMiddleware()

    mw._maybe_verify_present_files(_state(sandbox_id="local:thread-xyz"), _config(), _present_msgs())
    assert fired == []


def test_non_present_tool_skipped(monkeypatch):
    oam._verified_present.clear()
    fired = _patch_create_task(monkeypatch)
    mw = ObserveAdjustMiddleware()

    msgs = [AIMessage(content="x"), ToolMessage("ok", tool_call_id="t1", name="bash")]
    mw._maybe_verify_present_files(_state(), _config(), msgs)
    assert fired == []
