"""Run-scope identity and its consumers.

The 2026-11-01 incident (run 3cc50519) produced 68M tokens and a destroyed
sandbox, and reconstructing which run wrote which line required guesswork:
sandbox.log JSON lines, ACP transport logs and the token middleware carried
no run identity. These tests pin the fix from both ends:

* :mod:`deerflow.runtime.run_context` — the task-scoped frame (set/reset,
  field omission) and its helpers.
* the consumers — ``sandbox.log`` lines only gain keys when a scope is set,
  and the token-usage middleware's log line names the model that actually
  answered plus the run scope.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools` import
# each other; entering from the workspace_tools side is the order the
# application itself uses (see tests/test_sandbox_observation_writer.py).
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.runtime import run_context as rc  # noqa: E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# run_context module semantics
# ---------------------------------------------------------------------------


async def test_set_and_reset_round_trip() -> None:
    token = rc.set_run_scope(run_id="r1", thread_id="t1", correlation_id="c1")
    try:
        assert rc.current_run_fields() == {"run_id": "r1", "thread_id": "t1", "correlation_id": "c1"}
    finally:
        rc.reset_run_scope(token)
    assert rc.current_run_fields() == {}


async def test_partial_scope_omits_missing_fields() -> None:
    token = rc.set_run_scope(run_id="r1")  # no thread/correlation
    try:
        assert rc.current_run_fields() == {"run_id": "r1"}
    finally:
        rc.reset_run_scope(token)


async def test_unset_scope_yields_empty_everything() -> None:
    assert rc.current_run_fields() == {}
    assert rc.format_scope() == ""
    assert rc.current_run_scope() is None


async def test_new_task_inherits_scope() -> None:
    # The property the worker relies on: the run task stamps its identity and
    # every task spawned from it reads the same scope.
    token = rc.set_run_scope(run_id="r1", thread_id="t1")

    async def stamped_child() -> dict:
        await asyncio.sleep(0)
        return rc.current_run_fields()

    try:
        child_result = await asyncio.create_task(stamped_child())
    finally:
        rc.reset_run_scope(token)
    assert child_result == {"run_id": "r1", "thread_id": "t1"}


async def test_format_scope_orders_fields() -> None:
    token = rc.set_run_scope(run_id="r1", thread_id="t1", correlation_id="c1")
    try:
        assert rc.format_scope() == "run_id=r1 correlation_id=c1 thread_id=t1"
    finally:
        rc.reset_run_scope(token)


# ---------------------------------------------------------------------------
# sandbox.log consumer: only stamped fields are added
# ---------------------------------------------------------------------------


@pytest.fixture
def thread_log(tmp_path, monkeypatch):
    """Route the sandbox.log writer at a throwaway path."""
    log_path = tmp_path / "sandbox.log"
    monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: "t-scope")
    monkeypatch.setattr(sandbox_tools, "_sandbox_log_file", lambda _tid: log_path)
    return log_path


async def test_sandbox_log_stamps_run_fields_when_scoped(thread_log) -> None:
    token = rc.set_run_scope(run_id="r-run", thread_id="t-scope", correlation_id="c-run")
    try:
        sandbox_tools._write_sandbox_observation("local:t-scope", "bash", None, "$ echo hi", output="hi")
    finally:
        rc.reset_run_scope(token)

    line = json.loads(thread_log.read_text().splitlines()[-1])
    assert line["run_id"] == "r-run"
    assert line["correlation_id"] == "c-run"
    assert line["thread_id"] == "t-scope"
    # The legacy fields are untouched.
    assert line["type"] == "bash"
    assert line["summary"] == "$ echo hi"


async def test_sandbox_log_is_byte_identical_without_scope(thread_log) -> None:
    sandbox_tools._write_sandbox_observation("local:t-scope", "bash", None, "$ ls", output="x")
    line = json.loads(thread_log.read_text().splitlines()[-1])
    assert "run_id" not in line
    assert "correlation_id" not in line


# ---------------------------------------------------------------------------
# token_usage_middleware: model attribution + run scope in the log line
# ---------------------------------------------------------------------------


def _usage_middleware():
    from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware

    return TokenUsageMiddleware()


def _runtime():
    from langgraph.runtime import Runtime

    return Runtime(context={}, store=None)


async def test_token_usage_log_line_names_model_and_run(caplog) -> None:
    import logging

    from langchain_core.messages import AIMessage

    mw = _usage_middleware()
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"model_name": "MiniMax-M3"},
    )
    state = {"messages": [message], "todos": []}
    scope = rc.set_run_scope(run_id="r-att", thread_id="t-att")
    try:
        with caplog.at_level(logging.INFO, logger="deerflow.agents.middlewares.token_usage_middleware"):
            mw.after_model(state, _runtime())
    finally:
        rc.reset_run_scope(scope)

    matching = [r for r in caplog.records if "LLM token usage" in r.getMessage()]
    assert matching, "token usage log line missing"
    text = matching[-1].getMessage()
    assert "model=MiniMax-M3" in text, text
    assert "run_id=r-att" in text, text


async def test_token_usage_log_line_omits_scope_when_unset(caplog) -> None:
    import logging

    from langchain_core.messages import AIMessage

    mw = _usage_middleware()
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        response_metadata={"model_name": "m2"},
    )
    state = {"messages": [message], "todos": []}
    with caplog.at_level(logging.INFO, logger="deerflow.agents.middlewares.token_usage_middleware"):
        mw.after_model(state, _runtime())

    matching = [r for r in caplog.records if "LLM token usage" in r.getMessage()]
    assert matching
    text = matching[-1].getMessage()
    assert "model=m2" in text
    assert "run_id=" not in text


# ---------------------------------------------------------------------------
# background-subagent cancellation is thread-scoped
# ---------------------------------------------------------------------------


async def test_cancel_background_tasks_for_thread_is_thread_scoped() -> None:
    import threading

    from deerflow.subagents import executor
    from deerflow.subagents.executor import SubagentResult, SubagentStatus, cancel_background_tasks_for_thread

    def make(task_id: str, thread_id: str, terminal: bool) -> SubagentResult:
        result = SubagentResult(task_id=task_id, trace_id="tr", status=SubagentStatus.PENDING, thread_id=thread_id)
        result.cancel_event = threading.Event()
        if terminal:
            result.try_set_terminal(SubagentStatus.COMPLETED, result="done")
        return result

    live_a = make("a1", "thread-a", terminal=False)
    live_b = make("b1", "thread-b", terminal=False)
    done_a = make("a2", "thread-a", terminal=True)

    snapshot = {r.task_id: r for r in [live_a, live_b, done_a]}
    original = executor._background_tasks
    executor._background_tasks = snapshot  # type: ignore[assignment]
    try:
        signalled = cancel_background_tasks_for_thread("thread-a")
    finally:
        executor._background_tasks = original

    assert signalled == 1
    assert live_a.cancel_event.is_set(), "matching live task must be signalled"
    assert not live_b.cancel_event.is_set(), "other thread's task must be untouched"
    assert not done_a.cancel_event.is_set(), "terminal task must not be signalled"
