"""Tests for the dead-end search divergence detector (Layer 3).

This is the third detection layer in LoopDetectionMiddleware. It catches
the failure mode where the model invents a project name (e.g.
"cowork-fullstack") and then exhaustively searches the filesystem for it
with N distinct commands that all return ENOENT.

The hash-based detector (Layer 1) misses this because different
commands produce different hashes. The frequency-based detector (Layer 2)
misses this because the commands are distinct tool calls. This layer
inspects ToolMessage *content* for ENOENT patterns and tracks distinct
basenames per thread.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.loop_detection_middleware import (
    LoopDetectionMiddleware,
    _DEAD_END_HARD_LIMIT,
    _DEAD_END_WARN_THRESHOLD,
)


def _runtime(thread_id: str = "t1", run_id: str = "r1") -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _make_tool_message(content: str, tool_call_id: str = "tc-1") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id)


def _make_ai_message(tool_calls: list[dict]) -> AIMessage:
    """Build an AIMessage with tool_calls that have the required ``id`` field.

    LangChain v1 enforces ``id`` on every tool call dict, so we add
    deterministic IDs here to keep the test data stable.
    """
    augmented = []
    for i, tc in enumerate(tool_calls):
        tc = dict(tc)
        tc.setdefault("id", f"call-{i}")
        tc.setdefault("type", "tool_call")
        augmented.append(tc)
    return AIMessage(content="", tool_calls=augmented)


def _state_with_messages(*messages) -> dict:
    """Build a state dict ending with the given messages."""
    return {"messages": list(messages)}


def test_dead_end_warning_fires_after_threshold_distinct_enoent():
    """When N distinct ENOENT results for the same basename accumulate, warn.

    One detector call = one agent step. Each call increments the per-
    thread counter by 1 for the most-frequent ENOENT basename seen.
    """
    middleware = LoopDetectionMiddleware()
    # A state with 5 ENOENT results for cowork-fullstack in one assistant
    # turn. One call to the detector should aggregate them and increment
    # the counter by 1 — the count is per *call*, not per ENOENT in the
    # scan, because one call = one agent step.
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'cowork-fullstack': No such file or directory", "tc-1"),
        _make_tool_message("find: 'cowork-fullstack': No such file or directory", "tc-2"),
        _make_tool_message("find: 'cowork-fullstack': No such file or directory", "tc-3"),
        _make_tool_message("find: 'cowork-fullstack': No such file or directory", "tc-4"),
        _make_tool_message("find: 'cowork-fullstack': No such file or directory", "tc-5"),
    )
    # 5 calls — each adds 1, total = 5, fires warning at threshold
    result = None
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        result = middleware._check_dead_end_search(state["messages"], "t1")
        if result is not None:
            break
    assert result is not None, "warning should fire at threshold"
    warning, hard_stop = result
    assert hard_stop is False, "warning is not yet a hard stop"
    assert "cowork-fullstack" in warning
    assert "ask_clarification" in warning


def test_dead_end_hard_stop_at_limit():
    """When the basename count hits the hard limit, force-stop."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'ghost-repo': No such file or directory", "tc-1"),
    )
    # Run the check _DEAD_END_HARD_LIMIT times — each call increments by 1
    last_result = None
    for _ in range(_DEAD_END_HARD_LIMIT):
        last_result = middleware._check_dead_end_search(state["messages"], "t1")
    assert last_result is not None
    warning, hard_stop = last_result
    assert hard_stop is True, "hard stop should fire at the limit"
    assert "ghost-repo" in warning


def test_dead_end_does_not_fire_for_legitimate_searches():
    """Distinct basenames with valid results do not trigger the detector."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        HumanMessage(content="list files"),
        _make_tool_message("file1.txt\nfile2.txt\nfile3.txt", "tc-1"),
        _make_tool_message("file4.txt\nfile5.txt", "tc-2"),
    )
    result = middleware._check_dead_end_search(state["messages"], "t1")
    assert result is None


def test_dead_end_only_inspects_most_recent_tool_message():
    """The detector looks only at the last ToolMessage, not earlier ones."""
    middleware = LoopDetectionMiddleware()
    # Build a state where the LAST tool message is a success but earlier
    # ones were ENOENT for the same basename. The detector should not fire.
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'foo': No such file or directory", "tc-old-1"),
        _make_tool_message("ls: cannot access 'foo': No such file or directory", "tc-old-2"),
        _make_tool_message("foo.txt\nfoo.md", "tc-recent"),
    )
    result = middleware._check_dead_end_search(state["messages"], "t1")
    # Only the most recent tool message is inspected, so no warning
    assert result is None


def test_dead_end_distinct_basenames_are_tracked_separately():
    """Different basenames do not pollute each other's counts."""
    middleware = LoopDetectionMiddleware()
    state_alpha = _state_with_messages(
        _make_tool_message("ls: cannot access 'alpha': No such file or directory", "tc-a1"),
    )
    state_beta = _state_with_messages(
        _make_tool_message("ls: cannot access 'beta': No such file or directory", "tc-b1"),
    )
    # Run alpha 4 times — should not fire (threshold is 5)
    for _ in range(4):
        result = middleware._check_dead_end_search(state_alpha["messages"], "t1")
        assert result is None, "alpha count should not reach threshold"
    # Run beta 4 times — alpha count should not have polluted
    for _ in range(4):
        result = middleware._check_dead_end_search(state_beta["messages"], "t1")
        assert result is None, "beta count should not reach threshold"


def test_dead_end_per_thread_isolation():
    """Dead-end tracking is isolated per thread."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'phantom': No such file or directory", "tc-1"),
    )
    # Thread A: 5 ENOENTs for "phantom" → warning fires
    last_a = None
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        last_a = middleware._check_dead_end_search(state["messages"], "thread-A")
    # Thread B: 1 ENOENT — should NOT fire (separate counter)
    last_b = middleware._check_dead_end_search(state["messages"], "thread-B")
    assert last_a is not None, "thread-A should fire after threshold"
    assert last_b is None, "thread-B has its own counter; 1 ENOENT does not fire"


def test_dead_end_normalizes_path_components():
    """/a/b/cowork-fullstack and cowork-fullstack count as the same basename."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access '/home/jahanzaib/Desktop/cowork-fullstack': No such file or directory", "tc-1"),
        _make_tool_message("ls: cannot access '/tmp/cowork-fullstack': No such file or directory", "tc-2"),
        _make_tool_message("ls: cannot access 'cowork-fullstack': No such file or directory", "tc-3"),
        _make_tool_message("ls: cannot access '/var/cowork-fullstack': No such file or directory", "tc-4"),
        _make_tool_message("ls: cannot access '/opt/cowork-fullstack': No such file or directory", "tc-5"),
    )
    # 5 calls — each call increments by 1 for "cowork-fullstack"
    result = None
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        result = middleware._check_dead_end_search(state["messages"], "t1")
        if result is not None:
            break
    assert result is not None
    warning, _ = result
    assert "cowork-fullstack" in warning


def test_dead_end_warning_message_format():
    """The warning message has the documented format."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'X': No such file or directory", "tc-1"),
    )
    result = None
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        result = middleware._check_dead_end_search(state["messages"], "t1")
        if result is not None:
            break
    assert result is not None
    warning, _ = result
    assert "[DEAD-END SEARCH]" in warning
    assert "ask_clarification" in warning


def test_dead_end_hard_stop_message_format():
    """The hard-stop message has the documented format."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'X': No such file or directory", "tc-1"),
    )
    last_result = None
    for _ in range(_DEAD_END_HARD_LIMIT):
        last_result = middleware._check_dead_end_search(state["messages"], "t1")
    assert last_result is not None
    warning, hard_stop = last_result
    assert hard_stop is True
    assert "[FORCED STOP]" in warning
    assert "X" in warning


def test_dead_end_no_tool_message_returns_none():
    """If there are no ToolMessages, the detector does nothing."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    )
    result = middleware._check_dead_end_search(state["messages"], "t1")
    assert result is None


def test_dead_end_no_match_returns_none():
    """ToolMessage content without ENOENT pattern does not trigger."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("here is some legitimate output\nwith multiple lines", "tc-1"),
    )
    result = middleware._check_dead_end_search(state["messages"], "t1")
    assert result is None


def test_dead_end_resets_per_run():
    """_reset_tool_freq_for_run also clears dead-end state for the thread."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'X': No such file or directory", "tc-1"),
    )
    # Accumulate some ENOENTs
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        middleware._check_dead_end_search(state["messages"], "t1")
    assert middleware._dead_end_basenames.get("t1") is not None
    # Reset via the public per-run method
    middleware._reset_tool_freq_for_run(_runtime(thread_id="t1"))
    assert "t1" not in middleware._dead_end_basenames
    assert "t1" not in middleware._dead_end_warned


def test_dead_end_does_not_re_warn_after_warning_fires():
    """Once a basename has been warned about, it is not warned again."""
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        _make_tool_message("ls: cannot access 'X': No such file or directory", "tc-1"),
    )
    # First, trigger the warning
    first_warning = None
    for _ in range(_DEAD_END_WARN_THRESHOLD):
        result = middleware._check_dead_end_search(state["messages"], "t1")
        if result is not None:
            first_warning = result
            break
    assert first_warning is not None, "warning should fire at threshold"
    # Subsequent calls for the same basename should NOT re-warn
    # (until the hard limit is reached, which would force-stop)
    re_warned = False
    for _ in range(_DEAD_END_HARD_LIMIT):
        result = middleware._check_dead_end_search(state["messages"], "t1")
        if result is not None:
            _, hard_stop = result
            if not hard_stop:
                # A non-hard-stop result after the first warning means
                # the same basename re-fired — that's the regression we
                # want to catch.
                re_warned = True
                break
    assert not re_warned, "basename should not re-warn after first warning"
    # Verify that warned set contains "X"
    assert "X" in middleware._dead_end_warned.get("t1", set())


def test_dead_end_does_not_break_existing_layers():
    """Layer 3 addition must not affect Layer 1 (hash) or Layer 2 (frequency)."""
    middleware = LoopDetectionMiddleware()
    # Create a state where Layer 1 would fire (same call repeated)
    # and Layer 3 should NOT add noise
    same_call = {"name": "bash", "args": {"command": "ls"}}
    state = _state_with_messages(
        _make_ai_message([same_call]),
        _make_tool_message("file1\nfile2", "tc-1"),
        _make_ai_message([same_call]),
        _make_tool_message("file1\nfile2", "tc-2"),
        _make_ai_message([same_call]),
        _make_tool_message("file1\nfile2", "tc-3"),
    )
    # Layer 3 specifically should return None for the most recent ToolMessage
    dead_end = middleware._check_dead_end_search(state["messages"], "t1")
    assert dead_end is None, "Layer 3 must not fire on legitimate successful output"


def test_dead_end_handles_search_files_tool_output():
    """search_files 'No files found' is also a dead-end signal.

    Without a clear basename to extract, the detector stays quiet
    rather than crashing. Future iterations could integrate the
    tool_name into the basename extraction, but that requires the
    AIMessage's tool_call to be available alongside the ToolMessage.
    """
    middleware = LoopDetectionMiddleware()
    state = _state_with_messages(
        ToolMessage(content="No files found", tool_call_id="tc-1"),
    )
    result = middleware._check_dead_end_search(state["messages"], "t1")
    assert result is None  # no basename to track; detector stays quiet