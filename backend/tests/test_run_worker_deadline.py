"""A run must reach a terminal state even when nothing inside it ever returns.

Run 3cc50519 (2026-11-01) was still ``running`` after an hour, 311 LLM calls
and 68M tokens: a plan-mode lead agent fanning out subagents and an ACP
delegation has no natural end, and nothing bounded the run as a whole.
``recursion_limit`` counts lead-graph steps only (subagent graphs do not
count), and ``abort_event`` is checked *between* stream chunks, so a run
blocked inside one long tool call cannot be stopped by a flag either.

The worker therefore enforces a wall-clock deadline that cancels at the await
point, marks the run ``timeout`` with an actionable message, and publishes the
same ``error`` event the UI already renders for the step-budget case.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nova.runtime.runs.manager import RunManager
from nova.runtime.runs.schemas import RunStatus
from nova.runtime.runs.worker import DEFAULT_MAX_RUN_SECONDS, RunContext, resolve_max_run_seconds, run_agent

pytestmark = pytest.mark.anyio


def _bridge() -> SimpleNamespace:
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


async def _run(agent, *, max_run_seconds: float | None) -> tuple[RunManager, object, SimpleNamespace]:
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = _bridge()
    await asyncio.wait_for(
        run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, max_run_seconds=max_run_seconds),
            agent_factory=lambda *, config: agent,
            graph_input={},
            config={},
        ),
        timeout=10,  # the test fails, rather than hangs, if the deadline never fires
    )
    return run_manager, record, bridge


def _error_events(bridge) -> list[dict]:
    return [c.args[2] for c in bridge.publish.await_args_list if c.args[1] == "error"]


class HangsBeforeFirstChunk:
    """A tool call that never returns, before anything was streamed."""

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        await asyncio.Event().wait()
        yield  # pragma: no cover


class HangsAfterFirstChunk:
    """Some progress, then a call that never returns."""

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        yield {"messages": []}
        await asyncio.Event().wait()


class SwallowsCancellation:
    """Code that catches the cancellation and keeps going.

    The cancel the deadline raises is a request, not a guarantee: a tool or
    middleware that eats ``CancelledError`` would keep the stream alive. The
    worker must still stop at the next chunk instead of running forever.
    """

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        while True:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass
            yield {"messages": []}


async def test_a_run_hung_before_streaming_is_ended_as_timeout():
    run_manager, record, bridge = await _run(HangsBeforeFirstChunk(), max_run_seconds=0.2)
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.timeout
    assert "time limit" in fetched.error
    assert "continue" in fetched.error, "the message must say how to resume from the saved checkpoint"
    events = _error_events(bridge)
    assert len(events) == 1
    assert events[0]["name"] == "RunDeadlineExceeded"
    bridge.publish_end.assert_awaited_once()


async def test_a_run_hung_mid_stream_is_ended_as_timeout():
    run_manager, record, _ = await _run(HangsAfterFirstChunk(), max_run_seconds=0.2)
    assert (await run_manager.get(record.run_id)).status == RunStatus.timeout


async def test_swallowed_cancellation_still_ends_the_run():
    run_manager, record, bridge = await _run(SwallowsCancellation(), max_run_seconds=0.2)
    assert (await run_manager.get(record.run_id)).status == RunStatus.timeout
    assert [e["name"] for e in _error_events(bridge)] == ["RunDeadlineExceeded"]


async def test_a_run_that_finishes_in_time_is_untouched():
    class Quick:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    run_manager, record, bridge = await _run(Quick(), max_run_seconds=5)
    assert (await run_manager.get(record.run_id)).status == RunStatus.success
    assert _error_events(bridge) == []


async def test_an_unrelated_timeout_inside_the_run_is_a_plain_error():
    """Only *our* deadline means ``timeout``; a TimeoutError raised by code
    inside the run is that code's failure and keeps its own message."""

    class InnerTimeout:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise TimeoutError("upstream API did not answer")
            yield  # pragma: no cover

    run_manager, record, bridge = await _run(InnerTimeout(), max_run_seconds=5)
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.error
    assert fetched.error == "upstream API did not answer"
    assert [e["name"] for e in _error_events(bridge)] == ["TimeoutError"]


async def test_zero_disables_the_deadline():
    class Slow:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            await asyncio.sleep(0.3)
            yield {"messages": []}

    run_manager, record, _ = await _run(Slow(), max_run_seconds=0)
    assert (await run_manager.get(record.run_id)).status == RunStatus.success


def test_resolution_order(monkeypatch):
    monkeypatch.delenv("NOVA_RUN_MAX_SECONDS", raising=False)
    assert resolve_max_run_seconds(RunContext(checkpointer=None)) == DEFAULT_MAX_RUN_SECONDS
    monkeypatch.setenv("NOVA_RUN_MAX_SECONDS", "900")
    assert resolve_max_run_seconds(RunContext(checkpointer=None)) == 900
    # An explicit context value wins over the environment.
    assert resolve_max_run_seconds(RunContext(checkpointer=None, max_run_seconds=60)) == 60
    # 0 (or negative) means "no deadline".
    monkeypatch.setenv("NOVA_RUN_MAX_SECONDS", "0")
    assert resolve_max_run_seconds(RunContext(checkpointer=None)) is None
    # A typo must not silently disable the safety net.
    monkeypatch.setenv("NOVA_RUN_MAX_SECONDS", "two hours")
    assert resolve_max_run_seconds(RunContext(checkpointer=None)) == DEFAULT_MAX_RUN_SECONDS


def test_default_outlasts_the_per_unit_budgets():
    """The run bound must not undercut the bounds of the work inside it:
    a subagent may take ``subagents.timeout_seconds`` (1800) and an ACP turn
    ``turn_timeout`` (1800), and a plan-mode run chains several of them."""
    assert DEFAULT_MAX_RUN_SECONDS >= 3 * 1800
