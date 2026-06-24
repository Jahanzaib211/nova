"""Tests for the manifest injection in ThreadDataMiddleware.

The middleware runs at agent-run start and prepending a SystemMessage
that carries the canonical self-knowledge block. These tests cover the
fire-once-per-thread contract and the non-fatal fallback.
"""

from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deerflow.agents.middlewares.thread_data_middleware import (
    ThreadDataMiddleware,
    _MANIFEST_TAG,
)


def _fake_runtime():
    """Build a minimal Runtime stand-in for the middleware's before_agent.

    The middleware reads ``runtime.context`` for thread_id and run_id.
    Real Runtime objects come from LangGraph; here we use a SimpleNamespace
    so the test stays in pure-Python.
    """
    from types import SimpleNamespace

    return SimpleNamespace(context={"thread_id": "test-thread", "run_id": "test-run"})


def test_manifest_injected_on_first_run():
    """On a fresh thread (no existing manifest), the middleware prepends one."""
    middleware = ThreadDataMiddleware()
    state = {
        "messages": [HumanMessage(content="hi")],
        "thread_data": None,
    }
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    msgs = result["messages"]
    assert len(msgs) == 2
    first = msgs[0]
    assert isinstance(first, SystemMessage), f"first message must be SystemMessage, got {type(first).__name__}"
    assert _MANIFEST_TAG in first.content


def test_manifest_not_reinjected_on_subsequent_turns():
    """Once injected, the middleware must not re-inject on later turns."""
    middleware = ThreadDataMiddleware()
    # Simulate the post-injection state from a previous turn
    existing_manifest = SystemMessage(content=f"{_MANIFEST_TAG}\nproject root: /tmp\n</agent_manifest>")
    state = {
        "messages": [existing_manifest, HumanMessage(content="hi again")],
        "thread_data": None,
    }
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    msgs = result["messages"]
    # No new SystemMessage added; existing manifest preserved
    system_count = sum(1 for m in msgs if isinstance(m, SystemMessage))
    assert system_count == 1, f"manifest re-injected; SystemMessage count = {system_count}"
    # Order preserved
    assert msgs[0] is existing_manifest
    assert isinstance(msgs[-1], HumanMessage)


def test_manifest_injection_is_non_fatal_on_build_failure(monkeypatch):
    """If build_agent_manifest raises, the middleware still proceeds."""

    def _boom():
        raise RuntimeError("simulated manifest failure")

    monkeypatch.setattr(
        "deerflow.agents.middlewares.thread_data_middleware.build_agent_manifest",
        _boom,
    )
    middleware = ThreadDataMiddleware()
    state = {
        "messages": [HumanMessage(content="hi")],
        "thread_data": None,
    }
    # Should NOT raise — the injection is non-fatal by contract
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    msgs = result["messages"]
    # No manifest was injected; original HumanMessage preserved
    assert len(msgs) == 1
    assert isinstance(msgs[0], HumanMessage)


def test_manifest_detected_via_substring_in_non_manifest_system_message():
    """A SystemMessage that happens to contain the tag is treated as the manifest.

    This protects against the middleware double-injecting when the manifest
    has been concatenated into another system message (e.g. by a future
    prompt template that inlines the manifest block).
    """
    middleware = ThreadDataMiddleware()
    state = {
        "messages": [
            SystemMessage(content=f"You are helpful. {_MANIFEST_TAG} some manifest stuff"),
            AIMessage(content="ok"),
            HumanMessage(content="next"),
        ],
        "thread_data": None,
    }
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    msgs = result["messages"]
    # No new SystemMessage added
    system_count = sum(1 for m in msgs if isinstance(m, SystemMessage))
    assert system_count == 1


def test_middleware_does_not_modify_human_message_run_id(monkeypatch):
    """The existing run_id/timestamp stamping on HumanMessage still works."""
    middleware = ThreadDataMiddleware()
    state = {
        "messages": [HumanMessage(content="hi", name="user-input")],
        "thread_data": None,
    }
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    msgs = result["messages"]
    # Second message (after the injected SystemMessage) is the stamped HumanMessage
    human = [m for m in msgs if isinstance(m, HumanMessage)][0]
    assert human.additional_kwargs.get("run_id") == "test-run"
    assert "timestamp" in human.additional_kwargs


def test_thread_data_paths_still_returned():
    """The original thread_data contract is preserved alongside manifest injection."""
    middleware = ThreadDataMiddleware()
    state = {
        "messages": [HumanMessage(content="hi")],
        "thread_data": None,
    }
    result = middleware.before_agent(state, _fake_runtime())  # type: ignore[arg-type]
    assert "thread_data" in result
    assert "workspace_path" in result["thread_data"]
    assert "uploads_path" in result["thread_data"]
    assert "outputs_path" in result["thread_data"]