"""Regression tests for graceful GraphRecursionError handling in run_agent.

Run 2ea08475 (2026-07-12) died with a raw ``GraphRecursionError: Recursion
limit of 1000 reached`` after 22 minutes of legitimate progress. The worker
must map that to a user-actionable step-budget error (the thread checkpoint
survives, so a follow-up message resumes) instead of a bare traceback string.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.errors import GraphRecursionError

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext, run_agent

pytestmark = pytest.mark.anyio


def _bridge() -> SimpleNamespace:
    return SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )


async def test_recursion_error_maps_to_step_budget_error():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = _bridge()

    class ExhaustedAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise GraphRecursionError("Recursion limit of 5000 reached without hitting a stop condition.")
            yield  # pragma: no cover — makes this an async generator

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=lambda *, config: ExhaustedAgent(),
        graph_input={},
        config={},
    )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.error is not None
    assert "step budget" in fetched.error
    assert "continue" in fetched.error, "error must tell the user how to resume"

    error_events = [c for c in bridge.publish.await_args_list if c.args[1] == "error"]
    assert len(error_events) == 1
    payload = error_events[0].args[2]
    assert payload["name"] == "GraphRecursionError"
    assert "step budget" in payload["message"]


async def test_generic_exception_still_maps_to_plain_error():
    """The specific handler must not swallow other exceptions."""
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = _bridge()

    class BrokenAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=lambda *, config: BrokenAgent(),
        graph_input={},
        config={},
    )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.error == "boom"
