"""A finishing run cancels only the subagents IT dispatched.

The worker's ``finally`` used to call ``cancel_background_tasks_for_thread``.
With ``multitask_strategy="interrupt"`` the interrupted run A and its
replacement run B share a thread, and A's teardown routinely runs after B has
started — so A's ``finally`` signalled B's freshly dispatched subagents too.
Each ``SubagentResult`` now carries the dispatching run's id and the worker
cancels by run.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nova.runtime.run_context import current_run_scope, reset_run_scope, set_run_scope
from nova.runtime.runs.manager import RunManager
from nova.runtime.runs.schemas import RunStatus
from nova.runtime.runs.worker import RunContext, run_agent
from nova.subagents import executor
from nova.subagents.config import SubagentConfig
from nova.subagents.executor import (
    SubagentExecutor,
    SubagentResult,
    SubagentStatus,
    cancel_background_tasks_for_run,
    cancel_background_tasks_for_thread,
)


def _result(task_id: str, *, thread_id: str, run_id: str | None, terminal: bool = False) -> SubagentResult:
    result = SubagentResult(task_id=task_id, trace_id="tr", status=SubagentStatus.PENDING, thread_id=thread_id, run_id=run_id)
    result.cancel_event = threading.Event()
    if terminal:
        result.try_set_terminal(SubagentStatus.COMPLETED, result="done")
    return result


@pytest.fixture
def background_tasks(monkeypatch):
    tasks: dict[str, SubagentResult] = {}
    monkeypatch.setattr(executor, "_background_tasks", tasks)
    return tasks


def _executor(**kwargs) -> SubagentExecutor:
    config = SubagentConfig(name="probe", description="probe", system_prompt="x")
    return SubagentExecutor(config=config, tools=[], thread_id="thread-1", **kwargs)


def test_cancel_by_run_leaves_other_runs_on_the_same_thread_alone(background_tasks):
    run_a = _result("a1", thread_id="thread-1", run_id="run-a")
    run_b = _result("b1", thread_id="thread-1", run_id="run-b")
    done_a = _result("a2", thread_id="thread-1", run_id="run-a", terminal=True)
    background_tasks.update({r.task_id: r for r in (run_a, run_b, done_a)})

    assert cancel_background_tasks_for_run("run-a") == 1
    assert run_a.cancel_event.is_set()
    assert not run_b.cancel_event.is_set(), "the replacement run's subagent must survive"
    assert not done_a.cancel_event.is_set()


def test_cancel_by_run_ignores_empty_ids(background_tasks):
    background_tasks["x"] = _result("x", thread_id="thread-1", run_id=None)
    assert cancel_background_tasks_for_run("") == 0
    assert not background_tasks["x"].cancel_event.is_set()


def test_thread_scoped_cancel_still_works(background_tasks):
    background_tasks["a"] = _result("a", thread_id="thread-1", run_id="run-a")
    background_tasks["b"] = _result("b", thread_id="thread-2", run_id="run-b")
    assert cancel_background_tasks_for_thread("thread-1") == 1
    assert background_tasks["a"].cancel_event.is_set()
    assert not background_tasks["b"].cancel_event.is_set()


def test_executor_stamps_the_dispatching_run(background_tasks, monkeypatch):
    # Keep the task from actually executing; only the stamping matters here.
    monkeypatch.setattr(executor._scheduler_pool, "submit", lambda *a, **k: None)
    token = set_run_scope(run_id="run-a", thread_id="thread-1")
    try:
        sub = _executor()
    finally:
        reset_run_scope(token)

    task_id = sub.execute_async("do things", task_id="t-1")
    assert background_tasks[task_id].run_id == "run-a"
    assert background_tasks[task_id].thread_id == "thread-1"


def test_explicit_run_id_wins_over_scope():
    token = set_run_scope(run_id="run-a")
    try:
        assert _executor(run_id="run-z").run_id == "run-z"
    finally:
        reset_run_scope(token)


def test_executor_outside_a_run_has_no_run_id():
    assert _executor().run_id is None


@pytest.mark.anyio
async def test_worker_finally_cancels_only_its_own_runs_subagents(background_tasks):
    other_run = _result("other", thread_id="thread-1", run_id="run-other")
    background_tasks["other"] = other_run

    class DispatchesASubagent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            scope = current_run_scope()
            background_tasks["mine"] = _result("mine", thread_id="thread-1", run_id=scope.run_id if scope else None)
            yield {"messages": []}

    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    await asyncio.wait_for(
        run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, max_run_seconds=5),
            agent_factory=lambda *, config: DispatchesASubagent(),
            graph_input={},
            config={},
        ),
        timeout=10,
    )

    assert (await run_manager.get(record.run_id)).status == RunStatus.success
    assert background_tasks["mine"].run_id == record.run_id
    assert background_tasks["mine"].cancel_event.is_set(), "the run's own subagent must be cancelled"
    assert not other_run.cancel_event.is_set(), "another run's subagent on the same thread must not be"
