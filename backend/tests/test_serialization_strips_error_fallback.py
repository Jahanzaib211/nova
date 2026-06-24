"""Tests for the LLM error-fallback strip filter + middleware.

Two layers of defence against the synthetic error-fallback message
contamination:

  1. ``runtime.serialization.strip_deerflow_error_fallback_messages``
     — pure function that drops messages with
     ``additional_kwargs.deerflow_error_fallback == True``.
     Used on the UI/REST path (chat history, API responses).

  2. ``agents.middlewares.strip_error_fallback_middleware.StripErrorFallbackMiddleware``
     — runs in ``before_model`` and applies the same filter to the
     model-input path (next resume of a contaminated thread).

Both layers are needed because each addresses a different consumer:
the UI sees messages via the REST serializer; the LLM sees messages via
the in-process state.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.agents.middlewares.strip_error_fallback_middleware import (
    StripErrorFallbackMiddleware,
)
from deerflow.runtime.serialization import (
    serialize_channel_values_for_api,
    strip_deerflow_error_fallback_messages,
)


def _fallback_msg(text="LLM provider rejected the request"):
    return AIMessage(
        content=text,
        additional_kwargs={
            "deerflow_error_fallback": True,
            "error_type": "QuotaExceeded",
            "error_reason": "quota",
            "error_detail": "Account out of quota",
        },
    )


def _normal_msg(text):
    return AIMessage(content=text)


def _runtime(thread_id="t1", run_id="r1"):
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


# ───────────────────────────────────────────────────────────────────────
# Layer 1: serializer filter — pure function
# ───────────────────────────────────────────────────────────────────────


def test_serializer_drops_fallback_from_langchain_objects():
    msgs = [_normal_msg("hi"), _fallback_msg("oops"), _normal_msg("done")]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert len(out) == 2
    for m in out:
        assert getattr(m, "additional_kwargs", {}).get("deerflow_error_fallback") is not True


def test_serializer_drops_fallback_from_dicts():
    msgs = [
        {"type": "ai", "content": "hi", "additional_kwargs": {}},
        {"type": "ai", "content": "oops", "additional_kwargs": {"deerflow_error_fallback": True}},
        {"type": "ai", "content": "done", "additional_kwargs": {}},
    ]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert len(out) == 2
    for m in out:
        assert m.get("additional_kwargs", {}).get("deerflow_error_fallback") is not True


def test_serializer_handles_mixed_types():
    msgs = [
        _normal_msg("first"),
        {"type": "ai", "content": "second", "additional_kwargs": {"deerflow_error_fallback": True}},
        _normal_msg("third"),
    ]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert len(out) == 2


def test_serializer_keeps_messages_with_no_additional_kwargs():
    msgs = [
        HumanMessage(content="user input"),
        _fallback_msg(),
        HumanMessage(content="more"),
    ]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert len(out) == 2


def test_serializer_keeps_message_when_fallback_flag_is_false():
    msg = AIMessage(
        content="not a fallback",
        additional_kwargs={"deerflow_error_fallback": False},
    )
    out = strip_deerflow_error_fallback_messages([msg])
    assert len(out) == 1


def test_serializer_handles_all_message_types():
    msgs = [
        HumanMessage(content="x", additional_kwargs={"deerflow_error_fallback": True}),
        SystemMessage(content="x", additional_kwargs={"deerflow_error_fallback": True}),
        ToolMessage(content="x", tool_call_id="t1", additional_kwargs={"deerflow_error_fallback": True}),
        _normal_msg("ok"),
    ]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert len(out) == 1


def test_serializer_preserves_ordering():
    msgs = [
        _normal_msg("a"),
        _fallback_msg("drop1"),
        _normal_msg("b"),
        _fallback_msg("drop2"),
        _normal_msg("c"),
    ]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert [getattr(m, "content", None) for m in out] == ["a", "b", "c"]


def test_serializer_empty_list():
    assert strip_deerflow_error_fallback_messages([]) == []


def test_serializer_does_not_crash_on_garbage():
    msgs = [None, 42, "string", object()]
    out = strip_deerflow_error_fallback_messages(msgs)
    assert out == msgs


# ───────────────────────────────────────────────────────────────────────
# Layer 2: API serializer end-to-end
# ───────────────────────────────────────────────────────────────────────


def test_serialize_channel_values_for_api_drops_fallback():
    state = {
        "messages": [
            _normal_msg("first"),
            _fallback_msg(),
            _normal_msg("last"),
        ]
    }
    serialized = serialize_channel_values_for_api(state)
    assert "messages" in serialized
    msgs = serialized["messages"]
    assert len(msgs) == 2
    for m in msgs:
        additional_kwargs = m.get("additional_kwargs") or {}
        assert additional_kwargs.get("deerflow_error_fallback") is not True


def test_serialize_channel_values_for_api_preserves_normal_messages():
    state = {
        "messages": [
            {"type": "ai", "content": "a", "additional_kwargs": {}},
            {"type": "ai", "content": "b", "additional_kwargs": {"hide_from_ui": True}},
        ]
    }
    serialized = serialize_channel_values_for_api(state)
    msgs = serialized["messages"]
    assert len(msgs) == 2


# ───────────────────────────────────────────────────────────────────────
# Layer 3: middleware — model input path
# ───────────────────────────────────────────────────────────────────────


def test_middleware_no_op_when_no_contamination():
    mw = StripErrorFallbackMiddleware()
    state = {"messages": [_normal_msg("hi"), _normal_msg("done")]}
    result = mw.before_model(state, _runtime())
    assert result is None


def test_middleware_drops_fallback_messages_from_model_input():
    mw = StripErrorFallbackMiddleware()
    state = {"messages": [_normal_msg("hi"), _fallback_msg("oops"), _normal_msg("done")]}
    result = mw.before_model(state, _runtime())
    assert result is not None
    msgs = result["messages"]
    assert len(msgs) == 2
    for m in msgs:
        assert getattr(m, "additional_kwargs", {}).get("deerflow_error_fallback") is not True


def test_middleware_empty_messages_no_crash():
    mw = StripErrorFallbackMiddleware()
    result = mw.before_model({"messages": []}, _runtime())
    assert result is None


def test_middleware_no_messages_key():
    mw = StripErrorFallbackMiddleware()
    result = mw.before_model({}, _runtime())
    assert result is None


def test_middleware_non_fatal_on_exception(monkeypatch):
    mw = StripErrorFallbackMiddleware()

    def _boom(*a, **k):
        raise RuntimeError("simulated strip failure")

    monkeypatch.setattr(mw, "_strip_error_messages", _boom)
    state = {"messages": [_fallback_msg()]}
    result = mw.before_model(state, _runtime())
    assert result is None


def test_middleware_handles_dict_serialized_messages():
    mw = StripErrorFallbackMiddleware()
    state = {
        "messages": [
            {"type": "ai", "content": "hi", "additional_kwargs": {}},
            {"type": "ai", "content": "oops", "additional_kwargs": {"deerflow_error_fallback": True}},
        ]
    }
    result = mw.before_model(state, _runtime())
    assert result is not None
    assert len(result["messages"]) == 1


# ───────────────────────────────────────────────────────────────────────
# End-to-end: contamination cycle broken
# ───────────────────────────────────────────────────────────────────────


def test_end_to_end_contamination_cleaned_on_resume():
    mw = StripErrorFallbackMiddleware()
    contaminated_state = {
        "messages": [
            HumanMessage(content="hi"),
            _fallback_msg("The configured LLM provider rejected the request..."),
            HumanMessage(content="resume please"),
        ]
    }

    # Layer 1: UI sees clean state
    serialized = serialize_channel_values_for_api(contaminated_state)
    ui_msgs = serialized["messages"]
    for m in ui_msgs:
        additional_kwargs = m.get("additional_kwargs") or {}
        assert additional_kwargs.get("deerflow_error_fallback") is not True

    # Layer 2: LLM sees clean state
    llm_state = mw.before_model(contaminated_state, _runtime())
    llm_msgs = llm_state["messages"]
    for m in llm_msgs:
        assert getattr(m, "additional_kwargs", {}).get("deerflow_error_fallback") is not True
