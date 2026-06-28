"""Tests for ReflectFixBudgetMiddleware.

The middleware runtime-enforces the prompt's "iterate at most twice"
rule from the ``<self_verify>`` block. These tests assert:

- Budget counter resets on PASS (so a passing run doesn't carry failures).
- Budget counter increments on ISSUES.
- After budget is hit, a forced HumanMessage is queued and injected at
  the next wrap_model_call.
- Error results from dev_verify do NOT count as ISSUES (tooling
  failures aren't the agent's fault).
- Non-dev_verify tool calls do NOT touch the budget.
- Per-run reset at before_agent clears state.
- Per-thread isolation (two threads don't pollute each other).
- Non-fatal: a raise in after_model does not propagate.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.reflect_fix_middleware import (
    ReflectFixBudgetMiddleware,
)


def _runtime(thread_id: str = "t1", run_id: str = "r1") -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _make_ai_message(tool_calls: list[dict] | None = None, content: str = "") -> AIMessage:
    if tool_calls:
        augmented = []
        for i, tc in enumerate(tool_calls):
            tc = dict(tc)
            tc.setdefault("id", f"call-{i}")
            tc.setdefault("type", "tool_call")
            augmented.append(tc)
        return AIMessage(content=content, tool_calls=augmented)
    return AIMessage(content=content)


def _make_dev_verify_tool_message(content: str, tool_call_id: str = "tc-dv") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name="dev_verify")


def _make_other_tool_message(content: str, tool_call_id: str = "tc-other", name: str = "bash") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)


def _make_state_with_messages(*messages) -> dict:
    return {"messages": list(messages)}


def _issue_content(extra: str = "") -> str:
    """A dev_verify result that says ISSUES."""
    return (
        "# dev_verify — senior verification battery\n"
        "\n"
        "**Verdict: ⚠️ ISSUES — fix before shipping**\n"
        "\n"
        f"## Tests: ✗ fail\n```\nEXIT:1\n```\n{extra}"
    )


def _pass_content(extra: str = "") -> str:
    """A dev_verify result that says PASS."""
    return (
        "# dev_verify — senior verification battery\n"
        "\n"
        "**Verdict: ✅ PASS**\n"
        "\n"
        f"## Tests: ✓ pass\n```\nEXIT:0\n```\n{extra}"
    )


# ───────────────────────────────────────────────────────────────────────
# Core budget mechanics
# ───────────────────────────────────────────────────────────────────────


def test_first_issues_drives_a_fix_not_a_final_answer():
    """1st ISSUES: counter = 1, a DRIVE-to-green directive is queued (not a STOP)."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime())
    pending = mw._pending_warnings.get(("t1", "r1"))
    # A fix directive is queued — NOT the budget-hit "stop" message.
    assert pending is not None and len(pending) == 1
    assert "[VERIFY FAILED — FIX REQUIRED]" in pending[0]
    assert "[REFLECT BUDGET HIT]" not in pending[0]
    # Counter at 1
    assert mw._consecutive_issues[("t1", "r1")] == 1


def test_second_issues_hits_budget_and_queues_warning():
    """2nd consecutive ISSUES: counter = 2 = budget, forced message queued."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())
    # Two directives queued: a DRIVE (1st) then the budget-hit STOP (2nd).
    pending = mw._pending_warnings.get(("t1", "r1"))
    assert pending is not None and len(pending) == 2
    assert "[VERIFY FAILED — FIX REQUIRED]" in pending[0]
    assert "[REFLECT BUDGET HIT]" in pending[-1]
    assert "2 times" in pending[-1]


def test_third_issues_keeps_incrementing_counter_and_re_queues():
    """3rd ISSUES: counter keeps incrementing (capping happens at inject-time dedup)."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())
    # Counter increments past budget (it's just a count, not a cap)
    assert mw._consecutive_issues[("t1", "r1")] == 3
    # Three directives queued: DRIVE (1st) + STOP (2nd) + STOP (3rd).
    pending = mw._pending_warnings.get(("t1", "r1"))
    assert pending is not None and len(pending) == 3


def test_pass_resets_budget_to_zero():
    """After PASS, the counter resets to 0 (and the key is removed)."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state_issues = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state_issues, _runtime())
    assert mw._consecutive_issues[("t1", "r1")] == 1
    state_pass = _make_state_with_messages(_make_dev_verify_tool_message(_pass_content()))
    mw.after_model(state_pass, _runtime())
    assert ("t1", "r1") not in mw._consecutive_issues


def test_pass_resets_even_after_budget_hit():
    """If the agent somehow gets a PASS after hitting the budget, the budget resets."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state_issues = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state_issues, _runtime())
    mw.after_model(state_issues, _runtime())
    assert mw._pending_warnings.get(("t1", "r1")) is not None
    state_pass = _make_state_with_messages(_make_dev_verify_tool_message(_pass_content()))
    mw.after_model(state_pass, _runtime())
    assert ("t1", "r1") not in mw._consecutive_issues
    # Note: queued warnings are NOT drained by the PASS path — only by
    # wrap_model_call. This is intentional: if the agent's PASS came
    # AFTER the budget was already hit in this run, the agent still
    # gets the warning once. If the PASS comes in a fresh state (no
    # budget hit yet), nothing was queued in the first place.
    assert mw._pending_warnings.get(("t1", "r1")) is not None  # still queued


# ───────────────────────────────────────────────────────────────────────
# What does NOT count
# ───────────────────────────────────────────────────────────────────────


def test_error_result_does_not_count_as_issues():
    """dev_verify returning 'Error: ...' is a tooling failure, not agent failure."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(
        _make_dev_verify_tool_message("Error: sandbox not available"),
    )
    mw.after_model(state, _runtime())
    assert ("t1", "r1") not in mw._consecutive_issues
    assert mw._pending_warnings.get(("t1", "r1")) is None


def test_non_dev_verify_tool_does_not_touch_budget():
    """A bash or browser_check result doesn't change the dev_verify budget."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(
        _make_ai_message([{"name": "bash", "args": {"command": "ls"}}]),
        _make_other_tool_message("file1\nfile2", name="bash"),
    )
    mw.after_model(state, _runtime())
    assert ("t1", "r1") not in mw._consecutive_issues


def test_empty_messages_does_not_crash():
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    result = mw.after_model({"messages": []}, _runtime())
    assert result is None


def test_no_tool_message_does_not_crash():
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(
        HumanMessage(content="hi"),
        _make_ai_message(content="hello"),
    )
    result = mw.after_model(state, _runtime())
    assert result is None


def test_unrecognised_dev_verify_content_does_not_count():
    """If dev_verify returns content without PASS/ISSUES/Error markers, ignore."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(
        _make_dev_verify_tool_message("partial output without verdict"),
    )
    mw.after_model(state, _runtime())
    assert ("t1", "r1") not in mw._consecutive_issues


def test_non_string_content_does_not_crash():
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(
        ToolMessage(content=[{"type": "text", "text": "weird list content"}], tool_call_id="x", name="dev_verify"),
    )
    result = mw.after_model(state, _runtime())
    assert result is None


# ───────────────────────────────────────────────────────────────────────
# wrap_model_call injection
# ───────────────────────────────────────────────────────────────────────


def test_wrap_model_call_injects_queued_warning_into_messages():
    """When warnings are queued, wrap_model_call appends them at the end."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())
    # Now we have a queued warning. Simulate wrap_model_call by calling
    # the internal injection helper directly.
    from langchain.agents.middleware.types import ModelRequest

    # Build a minimal ModelRequest stand-in
    request = SimpleNamespace(
        messages=[_make_ai_message(content="hi")],
        runtime=_runtime(),
    )
    request.override = lambda messages: SimpleNamespace(messages=messages, runtime=request.runtime)

    new_request = mw._inject_into_request(request)
    # The last message should be the budget-hit HumanMessage
    assert isinstance(new_request.messages[-1], HumanMessage)
    assert "[REFLECT BUDGET HIT]" in new_request.messages[-1].content
    # Queue is now drained
    assert mw._pending_warnings.get(("t1", "r1")) is None


def test_wrap_model_call_no_op_when_no_warnings():
    """Without queued warnings, wrap_model_call is a no-op."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    request = SimpleNamespace(
        messages=[_make_ai_message(content="hi")],
        runtime=_runtime(),
    )
    request.override = lambda messages: SimpleNamespace(messages=messages, runtime=request.runtime)

    new_request = mw._inject_into_request(request)
    # Messages unchanged
    assert len(new_request.messages) == 1


def test_wrap_model_call_dedupes_multiple_warnings():
    """If multiple ISSUES fire, the dedup logic prevents duplicate injections."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())
    mw.after_model(state, _runtime())  # queues another warning
    # DRIVE (1st) + STOP@2 + STOP@3 = 3 queued (the two STOPs differ by count).
    assert len(mw._pending_warnings[("t1", "r1")]) == 3

    request = SimpleNamespace(
        messages=[_make_ai_message(content="hi")],
        runtime=_runtime(),
    )
    request.override = lambda messages: SimpleNamespace(messages=messages, runtime=request.runtime)

    new_request = mw._inject_into_request(request)
    # Two warnings → one HumanMessage with both (joined by \n\n)
    last = new_request.messages[-1]
    assert isinstance(last, HumanMessage)
    assert last.content.count("[REFLECT BUDGET HIT]") == 2


# ───────────────────────────────────────────────────────────────────────
# Per-run reset + per-thread isolation
# ───────────────────────────────────────────────────────────────────────


def test_before_agent_resets_budget_per_run():
    """A new run on the same thread starts with a clean budget."""
    mw = ReflectFixBudgetMiddleware()
    # First run
    mw.before_agent({}, _runtime("t1", "run-1"))
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime("t1", "run-1"))
    assert mw._consecutive_issues[("t1", "run-1")] == 1
    # Second run on same thread
    mw.before_agent({}, _runtime("t1", "run-2"))
    assert ("t1", "run-2") not in mw._consecutive_issues


def test_per_thread_isolation():
    """Thread A's budget does not pollute Thread B's budget."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime("thread-A", "r1"))
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime("thread-A", "r1"))
    mw.after_model(state, _runtime("thread-A", "r1"))
    # Thread A hit the budget
    assert mw._consecutive_issues[("thread-A", "r1")] == 2
    assert mw._pending_warnings.get(("thread-A", "r1")) is not None
    # Thread B has its own counter, untouched
    assert ("thread-B", "r1") not in mw._consecutive_issues


# ───────────────────────────────────────────────────────────────────────
# Non-fatal contract
# ───────────────────────────────────────────────────────────────────────


def test_after_model_exception_does_not_propagate(monkeypatch):
    """A raise inside after_model is swallowed (non-fatal contract)."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())

    def boom(*a, **k):
        raise RuntimeError("simulated")

    # Force _find_last_tool_message to raise
    monkeypatch.setattr(mw, "_find_last_tool_message", boom)
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    # Should not raise
    result = mw.after_model(state, _runtime())
    assert result is None


def test_invalid_budget_rejected():
    """Constructor rejects budget < 1."""
    import pytest

    with pytest.raises(ValueError, match="budget must be >= 1"):
        ReflectFixBudgetMiddleware(budget=0)
    with pytest.raises(ValueError, match="budget must be >= 1"):
        ReflectFixBudgetMiddleware(budget=-1)


def test_custom_budget():
    """Custom budget is respected (not just the default)."""
    mw = ReflectFixBudgetMiddleware(budget=5)
    mw.before_agent({}, _runtime())
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    for _ in range(4):
        mw.after_model(state, _runtime())
    # After 4 ISSUES, budget of 5 NOT yet hit → 4 DRIVE directives, no STOP.
    pending = mw._pending_warnings.get(("t1", "r1"))
    assert pending is not None and len(pending) == 4
    assert all("[VERIFY FAILED — FIX REQUIRED]" in p for p in pending)
    mw.after_model(state, _runtime())  # 5th → budget hit
    assert "[REFLECT BUDGET HIT]" in mw._pending_warnings[("t1", "r1")][-1]


def test_drive_directive_quotes_failing_items():
    """The under-budget DRIVE directive quotes the concrete failing lines."""
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime())
    content = (
        "# dev_verify\n**Verdict: ⚠️ ISSUES**\n"
        "## Tests: ✗ fail\n"
        "## Browser: ✗ render_error\n- / [render_error] — boom\n"
        "## Review: 1 HIGH\n- 🔴 risky thing\n"
    )
    state = _make_state_with_messages(_make_dev_verify_tool_message(content))
    mw.after_model(state, _runtime())
    directive = mw._pending_warnings[("t1", "r1")][0]
    assert "Failing items:" in directive
    assert "render_error" in directive
    assert "🔴 risky thing" in directive


def test_parse_dev_verify_issues_is_bounded_and_generic():
    """The parser keys off failure markers only and is capped (no project knowledge)."""
    big = "\n".join(f"- ✗ fail line {i}" for i in range(50))
    out = ReflectFixBudgetMiddleware._parse_dev_verify_issues(big)
    assert 0 < out.count("\n") <= 14  # capped at 15 lines
    # Non-failure content yields nothing.
    assert ReflectFixBudgetMiddleware._parse_dev_verify_issues("## Tests: ✓ pass\nall good") == ""


# ───────────────────────────────────────────────────────────────────────
# reset() public API
# ───────────────────────────────────────────────────────────────────────


def test_reset_clears_specific_thread():
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime("thread-A"))
    mw.before_agent({}, _runtime("thread-B"))
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime("thread-A"))
    mw.after_model(state, _runtime("thread-B"))
    mw.reset(thread_id="thread-A")
    assert ("thread-A", "r1") not in mw._consecutive_issues
    assert mw._consecutive_issues[("thread-B", "r1")] == 1


def test_reset_clears_all_when_no_thread_id():
    mw = ReflectFixBudgetMiddleware()
    mw.before_agent({}, _runtime("t1"))
    state = _make_state_with_messages(_make_dev_verify_tool_message(_issue_content()))
    mw.after_model(state, _runtime("t1"))
    mw.reset()
    assert mw._consecutive_issues == {}
    assert mw._pending_warnings == {}