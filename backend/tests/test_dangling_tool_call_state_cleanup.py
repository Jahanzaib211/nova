"""Tests for the state-level dangling tool call cleanup.

``DanglingToolCallMiddleware`` repairs a history whose tool calls never got a
result. The repair used to be transient: it was applied to the model-bound
request only, so every model call of a run re-detected (and re-logged) the same
dangling call, and the dangling calls accumulated turn after turn.

The middleware now also repairs the *persisted* agent state, so the repair is
applied once per run. These tests pin that behaviour:

* the synthetic ToolMessage lands IMMEDIATELY after the AIMessage that
  declared the call — the ``add_messages`` reducer must not move it to the end,
* the cleanup is idempotent,
* a clean history produces no state write at all,
* the provider ordering invariants survive the repair,
* the WARNING is emitted at most once per dangling tool-call id per run.
"""

import logging
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from nova.agents.middlewares.dangling_tool_call_middleware import (
    DanglingToolCallMiddleware,
)

LOGGER_NAME = "nova.agents.middlewares.dangling_tool_call_middleware"


def _runtime(thread_id: str = "t1", run_id: str = "r1"):
    """Runtime stand-in carrying the context the middleware keys its state on."""
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _ai(*tool_calls):
    return AIMessage(content="", tool_calls=list(tool_calls))


def _tc(name: str = "bash", tc_id: str = "call_1"):
    return {"name": name, "id": tc_id, "args": {}}


def _tool(tc_id: str, name: str = "bash"):
    return ToolMessage(content="result", tool_call_id=tc_id, name=name)


def _apply_cleanup(mw, messages, runtime=None):
    """Run the state cleanup and apply it through the real ``add_messages`` reducer.

    Returns ``(update, merged_messages)``. ``update`` is ``None`` when the
    middleware decided the history needed no repair.
    """
    state = {"messages": list(messages)}
    update = mw.before_model(state, runtime or _runtime())
    if update is None:
        return None, state["messages"]
    merged = add_messages(state["messages"], update["messages"])
    return update, merged


def _declared_ids(msg):
    return [tc["id"] for tc in (getattr(msg, "tool_calls", None) or [])]


def _assert_causal_order(messages):
    """Assert the ordering the OpenAI-compatible provider requires.

    * every declared tool-call id is answered by exactly one ToolMessage,
    * that ToolMessage sits immediately after the AIMessage that declared it,
    * no ToolMessage precedes the call it answers.
    """
    tool_ids = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]

    for index, msg in enumerate(messages):
        declared = _declared_ids(msg) if getattr(msg, "type", None) == "ai" else []
        if not declared:
            continue

        # The answers must directly follow the AIMessage, in declaration order.
        answered = []
        for follower in messages[index + 1 :]:
            if not isinstance(follower, ToolMessage):
                break
            answered.append(follower.tool_call_id)
        assert answered == declared, f"{declared} answered by {answered}"

        for tc_id in declared:
            assert tool_ids.count(tc_id) == 1, f"{tc_id} answered {tool_ids.count(tc_id)} times"

    # A ToolMessage must never sit before the call it answers.
    for index, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            continue
        declarers = [i for i, earlier in enumerate(messages) if getattr(earlier, "type", None) == "ai" and msg.tool_call_id in _declared_ids(earlier)]
        if declarers:
            assert index > declarers[0], f"{msg.tool_call_id} precedes its call"


class TestStateCleanupOrdering:
    def test_synthetic_tool_message_lands_immediately_after_its_ai_message(self):
        mw = DanglingToolCallMiddleware()
        messages = [
            HumanMessage(content="hi", id="h1"),
            _ai(_tc("bash", "call_1")),
            HumanMessage(content="still here", id="h2"),
        ]

        update, merged = _apply_cleanup(mw, messages)

        assert update is not None
        assert len(merged) == 4
        assert isinstance(merged[1], AIMessage)
        assert isinstance(merged[2], ToolMessage)
        assert merged[2].tool_call_id == "call_1"
        assert merged[2].status == "error"
        assert isinstance(merged[3], HumanMessage)
        assert merged[3].content == "still here"

    def test_state_update_uses_the_remove_all_sentinel(self):
        """The sentinel is what makes the reducer preserve the exact order.

        Without it ``add_messages`` APPENDS the new message to the end of the
        list, which breaks the causal order the provider requires.
        """
        mw = DanglingToolCallMiddleware()
        messages = [_ai(_tc("bash", "call_1"))]

        update, _merged = _apply_cleanup(mw, messages)

        assert update is not None
        written = update["messages"]
        assert written[0].id == REMOVE_ALL_MESSAGES
        assert isinstance(written[1], AIMessage)
        assert isinstance(written[2], ToolMessage)

    def test_naive_append_would_break_the_order(self):
        """Guards the reason the sentinel exists: a plain append lands at the end."""
        mw = DanglingToolCallMiddleware()
        messages = [
            HumanMessage(content="hi", id="h1"),
            _ai(_tc("bash", "call_1")),
            HumanMessage(content="still here", id="h2"),
        ]

        update, _merged = _apply_cleanup(mw, messages)
        synthetic = next(m for m in update["messages"] if isinstance(m, ToolMessage))
        naive = add_messages(messages, [synthetic])

        assert isinstance(naive[-1], ToolMessage)
        assert naive[-1].tool_call_id == "call_1"
        # ... while the sentinel keeps it next to the AIMessage.
        merged = add_messages(messages, update["messages"])
        assert isinstance(merged[2], ToolMessage)
        assert merged[2].tool_call_id == "call_1"

    def test_every_dangling_id_gets_exactly_one_tool_message(self):
        mw = DanglingToolCallMiddleware()
        messages = [
            _ai(_tc("bash", "call_1"), _tc("read", "call_2")),
            HumanMessage(content="interruption", id="h1"),
            _ai(_tc("ls", "call_3")),
        ]

        _update, merged = _apply_cleanup(mw, messages)

        tool_ids = [m.tool_call_id for m in merged if isinstance(m, ToolMessage)]
        assert sorted(tool_ids) == ["call_1", "call_2", "call_3"]
        assert len(tool_ids) == len(set(tool_ids))
        _assert_causal_order(merged)

    def test_multiple_dangling_calls_are_positioned_per_owning_ai_message(self):
        mw = DanglingToolCallMiddleware()
        messages = [
            _ai(_tc("bash", "call_1"), _tc("read", "call_2")),
            HumanMessage(content="interruption", id="h1"),
            _ai(_tc("ls", "call_3")),
        ]

        _update, merged = _apply_cleanup(mw, messages)

        assert isinstance(merged[0], AIMessage)
        assert merged[1].tool_call_id == "call_1"
        assert merged[2].tool_call_id == "call_2"
        assert isinstance(merged[3], HumanMessage)
        assert isinstance(merged[4], AIMessage)
        assert merged[5].tool_call_id == "call_3"
        _assert_causal_order(merged)


class TestStateCleanupIdempotence:
    def test_second_cleanup_is_a_no_op(self):
        mw = DanglingToolCallMiddleware()
        messages = [_ai(_tc("bash", "call_1")), HumanMessage(content="next", id="h1")]

        first_update, merged = _apply_cleanup(mw, messages)
        assert first_update is not None

        second_update, merged_again = _apply_cleanup(mw, merged)

        assert second_update is None
        assert merged_again == merged
        tool_msgs = [m for m in merged_again if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "call_1"

    def test_cleanup_does_not_duplicate_an_existing_tool_message(self):
        mw = DanglingToolCallMiddleware()
        messages = [_ai(_tc("bash", "call_1")), _tool("call_1")]

        update, merged = _apply_cleanup(mw, messages)

        assert update is None
        assert len([m for m in merged if isinstance(m, ToolMessage)]) == 1


class TestStateCleanupNoop:
    def test_clean_history_produces_no_state_update(self):
        mw = DanglingToolCallMiddleware()
        messages = [
            HumanMessage(content="hi", id="h1"),
            _ai(_tc("bash", "call_1")),
            _tool("call_1"),
            HumanMessage(content="next", id="h2"),
        ]

        update, merged = _apply_cleanup(mw, messages)

        assert update is None
        assert merged == messages

    def test_history_without_tool_calls_produces_no_state_update(self):
        mw = DanglingToolCallMiddleware()
        messages = [HumanMessage(content="hi", id="h1"), AIMessage(content="hello", id="a1")]

        update, merged = _apply_cleanup(mw, messages)

        assert update is None
        assert merged == messages

    def test_empty_state_produces_no_state_update(self):
        mw = DanglingToolCallMiddleware()

        assert mw.before_model({"messages": []}, _runtime()) is None
        assert mw.before_model({}, _runtime()) is None

    def test_cleanup_is_non_fatal_on_a_broken_state(self):
        """A malformed state must never raise into the run."""
        mw = DanglingToolCallMiddleware()

        assert mw.before_model({"messages": "not-a-list"}, _runtime()) is None
        assert mw.before_model(SimpleNamespace(), _runtime()) is None


class TestMixedHistoryInvariants:
    def test_orphan_and_late_tool_messages_keep_their_behaviour(self):
        mw = DanglingToolCallMiddleware()
        orphan = _tool("orphan_call", "orphan")
        messages = [
            HumanMessage(content="hi", id="h1"),
            _ai(_tc("bash", "call_1")),
            _ai(_tc("read", "call_2")),
            HumanMessage(content="interruption", id="h2"),
            _tool("call_1", "bash"),
            orphan,
            _ai(_tc("ls", "call_3")),
        ]

        _update, merged = _apply_cleanup(mw, messages)

        # The late result for call_1 moved up next to the call it answers.
        assert isinstance(merged[1], AIMessage)
        assert isinstance(merged[2], ToolMessage)
        assert merged[2].tool_call_id == "call_1"

        # The orphan (a ToolMessage with no matching call) is preserved in place.
        assert orphan in merged
        orphan_index = merged.index(orphan)
        assert isinstance(merged[orphan_index - 1], HumanMessage)
        assert merged[orphan_index - 1].content == "interruption"
        assert isinstance(merged[orphan_index + 1], AIMessage)

        # The dangling calls were answered.
        tool_ids = [m.tool_call_id for m in merged if isinstance(m, ToolMessage)]
        assert sorted(tool_ids) == ["call_1", "call_2", "call_3", "orphan_call"]
        _assert_causal_order(merged)

    def test_every_original_message_survives_the_cleanup(self):
        mw = DanglingToolCallMiddleware()
        messages = [
            HumanMessage(content="hi", id="h1"),
            _ai(_tc("bash", "call_1")),
            HumanMessage(content="interruption", id="h2"),
            _tool("call_1"),
            _tool("orphan_call", "orphan"),
        ]

        _update, merged = _apply_cleanup(mw, messages)

        for original in messages:
            assert original in merged


class TestWarningSuppression:
    def test_warning_is_emitted_once_per_dangling_id_per_run(self, caplog):
        mw = DanglingToolCallMiddleware()
        runtime = _runtime(thread_id="t-warn", run_id="r-warn")
        messages = [_ai(_tc("bash", "call_1"))]

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            first_update, merged = _apply_cleanup(mw, messages, runtime)
            assert first_update is not None
            first_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert len(first_warnings) == 1
            assert "call_1" in first_warnings[0].getMessage()

            # Second pass over the now-clean state: nothing to repair, no warning.
            second_update, _merged = _apply_cleanup(mw, merged, runtime)
            assert second_update is None
            assert [r for r in caplog.records if r.levelno == logging.WARNING] == first_warnings

    def test_repeated_repair_of_the_same_id_does_not_warn_again(self, caplog):
        """A history that stays broken (e.g. re-shaped by another middleware) warns once."""
        mw = DanglingToolCallMiddleware()
        runtime = _runtime(thread_id="t-repeat", run_id="r-repeat")
        messages = [_ai(_tc("bash", "call_1"))]

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for _ in range(5):
                mw.before_model({"messages": list(messages)}, runtime)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_different_runs_warn_independently(self, caplog):
        mw = DanglingToolCallMiddleware()
        messages = [_ai(_tc("bash", "call_1"))]

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            mw.before_model({"messages": list(messages)}, _runtime(thread_id="t", run_id="r-1"))
            mw.before_model({"messages": list(messages)}, _runtime(thread_id="t", run_id="r-2"))

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    def test_clean_state_never_warns(self, caplog):
        mw = DanglingToolCallMiddleware()
        runtime = _runtime(thread_id="t-clean", run_id="r-clean")
        messages = [_ai(_tc("bash", "call_1")), _tool("call_1")]

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for _ in range(10):
                assert mw.before_model({"messages": list(messages)}, runtime) is None

        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    def test_transient_repair_warns_once_per_id_per_run(self, caplog):
        """The model-bound safety net follows the same one-warning-per-id rule."""
        mw = DanglingToolCallMiddleware()
        runtime = _runtime(thread_id="t-transient", run_id="r-transient")
        messages = [_ai(_tc("bash", "call_1"))]

        def _request():
            request = SimpleNamespace(messages=list(messages), runtime=runtime)
            request.override = lambda messages: SimpleNamespace(messages=messages, runtime=runtime)
            return request

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for _ in range(5):
                mw.wrap_model_call(_request(), lambda req: "response")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_transient_repair_is_silent_once_the_state_is_clean(self, caplog):
        """After the state repair the model-bound request needs no patching at all."""
        mw = DanglingToolCallMiddleware()
        runtime = _runtime(thread_id="t-quiet", run_id="r-quiet")
        messages = [_ai(_tc("bash", "call_1"))]

        _update, merged = _apply_cleanup(mw, messages, runtime)

        overrides = []
        caplog.clear()

        def _request():
            request = SimpleNamespace(messages=list(merged), runtime=runtime)

            def _override(messages):
                overrides.append(messages)
                return SimpleNamespace(messages=messages, runtime=runtime)

            request.override = _override
            return request

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for _ in range(5):
                mw.wrap_model_call(_request(), lambda req: "response")

        assert overrides == []
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    def test_reported_ids_stay_bounded(self):
        """The bookkeeping must not grow without limit in a long-lived process."""
        mw = DanglingToolCallMiddleware()
        for index in range(DanglingToolCallMiddleware._MAX_TRACKED_RUNS * 3):
            mw.before_model(
                {"messages": [_ai(_tc("bash", f"call_{index}"))]},
                _runtime(thread_id="t", run_id=f"r-{index}"),
            )

        assert len(mw._reported_ids) <= DanglingToolCallMiddleware._MAX_TRACKED_RUNS
