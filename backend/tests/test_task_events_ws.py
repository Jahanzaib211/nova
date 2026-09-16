"""Thread-scoped task-event WebSocket — the transport for subagent/todo
correlation (WS-G).

Subagent lifecycle already flows as ``custom`` stream events (``task_started``
/ ``task_running`` / ``task_completed`` / …) produced inside the harness's
``task_tool``. This layer gives those events a *WebSocket* delivery path that
is scoped to the THREAD (not the transient run), so the panel can bind task
completions to specific todo rows even across reconnects:

- ``TaskEventHub`` — per-thread ring buffer + live subscribers;
- ``MirroringStreamBridge`` — wraps whatever StreamBridge the gateway runs and
  duplicates ``custom`` ``task_*`` events into the hub, resolving run→thread;
  everything else passes through untouched;
- ``/api/threads/{id}/tasks-ws`` — same-origin, session-authenticated,
  ownership-checked socket that replays the buffer and then streams live.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.task_events import MirroringStreamBridge, TaskEventHub

# ── Hub ─────────────────────────────────────────────────────────────────────


class TestTaskEventHub:
    @pytest.mark.asyncio
    async def test_live_subscriber_receives_published_events(self) -> None:
        hub = TaskEventHub()
        queue_gen = hub.subscribe("t1")
        task = asyncio.ensure_future(anext(queue_gen))

        await asyncio.sleep(0)  # let the subscriber register
        await hub.publish("t1", {"type": "task_started", "task_id": "x"})

        received = await asyncio.wait_for(task, timeout=2)
        assert received["type"] == "task_started"

    @pytest.mark.asyncio
    async def test_late_subscriber_replays_the_buffer(self) -> None:
        hub = TaskEventHub(buffer_size=8)
        await hub.publish("t1", {"type": "task_started", "task_id": "a"})
        await hub.publish("t1", {"type": "task_completed", "task_id": "a"})

        gen = hub.subscribe("t1")
        first = await asyncio.wait_for(anext(gen), timeout=2)
        second = await asyncio.wait_for(anext(gen), timeout=2)
        assert [first["type"], second["type"]] == ["task_started", "task_completed"]

    @pytest.mark.asyncio
    async def test_threads_are_isolated(self) -> None:
        hub = TaskEventHub()
        gen = hub.subscribe("t1")
        await asyncio.sleep(0)
        await hub.publish("t2", {"type": "task_started"})
        await hub.publish("t1", {"type": "task_running"})

        got = await asyncio.wait_for(anext(gen), timeout=2)
        assert got["type"] == "task_running"

    @pytest.mark.asyncio
    async def test_slow_consumer_does_not_block_or_break_publishers(self) -> None:
        hub = TaskEventHub(subscriber_queue_size=2)
        gen = hub.subscribe("t1")
        await asyncio.sleep(0)
        for i in range(10):
            await hub.publish("t1", {"i": i})
        # The subscriber queue overflowed; publisher never raised, and the
        # newest events are what a resumed reader sees.
        items = []
        try:
            while True:
                items.append(await asyncio.wait_for(anext(gen), timeout=0.05))
        except (TimeoutError, StopAsyncIteration):
            pass
        assert items[-1] == {"i": 9}


# ── Mirroring bridge ────────────────────────────────────────────────────────


class TestMirroringStreamBridge:
    def _wired(self):
        from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

        hub = TaskEventHub()
        inner = MemoryStreamBridge()

        async def resolve(run_id: str) -> str | None:
            return {"run-1": "thread-A"}.get(run_id)

        mirror = MirroringStreamBridge(
            inner=inner,
            hub=hub,
            resolve_run_thread=resolve,
        )
        return mirror, inner, hub

    @pytest.mark.asyncio
    async def test_task_custom_events_are_mirrored_to_the_thread(self) -> None:
        mirror, _inner, hub = self._wired()
        gen = hub.subscribe("thread-A")
        await asyncio.sleep(0)

        await mirror.publish("run-1", "custom", {"type": "task_completed", "task_id": "x"})
        got = await asyncio.wait_for(anext(gen), timeout=2)
        assert got == {"type": "task_completed", "task_id": "x"}

    @pytest.mark.asyncio
    async def test_non_task_custom_events_are_not_mirrored(self) -> None:
        mirror, _inner, hub = self._wired()
        gen = hub.subscribe("thread-A")
        await asyncio.sleep(0)

        await mirror.publish("run-1", "custom", {"type": "verify_result", "ok": True})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(gen), timeout=0.2)

    @pytest.mark.asyncio
    async def test_unresolvable_run_is_skipped_silently(self) -> None:
        mirror, _inner, hub = self._wired()
        gen = hub.subscribe("thread-A")
        await asyncio.sleep(0)
        await mirror.publish("run-unknown", "custom", {"type": "task_started"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(gen), timeout=0.2)

    @pytest.mark.asyncio
    async def test_delegation_still_delivers_everything(self) -> None:
        mirror, inner, _hub = self._wired()
        await mirror.publish("run-1", "values", {"messages": []})
        await mirror.publish_end("run-1")
        events = [e.data async for e in inner.subscribe("run-1") if e.event == "values"]
        assert events and events[0] == {"messages": []}

    @pytest.mark.asyncio
    async def test_malformed_custom_payload_is_ignored(self) -> None:
        mirror, _inner, hub = self._wired()
        gen = hub.subscribe("thread-A")
        await asyncio.sleep(0)
        await mirror.publish("run-1", "custom", "not-a-dict")
        await mirror.publish("run-1", "custom", {"no_type": True})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(gen), timeout=0.2)


# ── Route registration ──────────────────────────────────────────────────────


class TestRouteRegistration:
    def test_computer_ws_route_exists(self) -> None:
        from app.gateway.routers import threads as threads_router

        paths = {getattr(r, "path", "") for r in threads_router.router.routes}
        assert "/api/threads/{thread_id}/computer-ws" in paths


class TestComputerWsMultiplex:
    @pytest.mark.asyncio
    async def test_single_socket_carries_both_channels(self) -> None:
        from app.gateway.task_events import (
            TaskEventHub,
            run_computer_ws_stream,
        )

        tasks_hub = TaskEventHub()
        computer_hub = TaskEventHub(buffer_size=8)
        received: list[dict] = []

        from starlette.websockets import WebSocketState as _WsState

        class _FakeWs:
            def __init__(self) -> None:
                self.client_state = _WsState.CONNECTED

            async def accept(self):
                pass

            async def send_text(self, raw: str) -> None:
                payload = json.loads(raw)
                if payload.get("kind") == "stop":
                    self.client_state = _WsState.DISCONNECTED
                    raise RuntimeError("client gone")
                received.append(payload)

            async def close(self, code: int = 1000) -> None:
                self.client_state = _WsState.DISCONNECTED

        ws = _FakeWs()

        async def runner():
            await run_computer_ws_stream(ws, [tasks_hub, computer_hub], "t9")

        task = asyncio.ensure_future(runner())
        await asyncio.sleep(0)
        await tasks_hub.publish("t9", {"type": "task_completed", "task_id": "x"})
        await computer_hub.publish(
            "t9",
            {"channel": "browser", "kind": "dev_server", "status": "ready"},
        )
        # Sentinel tells the fake socket to hang up, which ends the stream.
        await computer_hub.publish("t9", {"channel": "workspace", "kind": "stop"})
        await asyncio.wait_for(task, timeout=3)

        kinds = {(p.get("type"), p.get("kind")) for p in received}
        assert ("task_completed", None) in kinds
        assert (None, "dev_server") in kinds


# ── todo binding extraction ─────────────────────────────────────────────────


class TestTodoIndexesExtraction:
    def test_in_progress_todos_are_bound(self) -> None:
        from deerflow.tools.builtins.task_tool import _in_progress_todo_indexes

        runtime = SimpleNamespace(
            state={
                "todos": [
                    {"content": "a", "status": "completed"},
                    {"content": "b", "status": "in_progress"},
                    {"content": "c"},
                ]
            },
            context=None,
            config={},
        )
        assert _in_progress_todo_indexes(runtime) == [1]

    def test_no_todos_is_empty_not_error(self) -> None:
        from deerflow.tools.builtins.task_tool import _in_progress_todo_indexes

        runtime = SimpleNamespace(state={}, context=None, config={})
        assert _in_progress_todo_indexes(runtime) == []

    def test_garbage_todos_do_not_raise(self) -> None:
        from deerflow.tools.builtins.task_tool import _in_progress_todo_indexes

        runtime = SimpleNamespace(state={"todos": ["junk", None, {"status": 7}]}, context=None, config={})
        assert _in_progress_todo_indexes(runtime) == []


class TestTaskEventsCarryBinding:
    """The binding must ride every terminal event so a frontend joining mid-run
    still learns which todos a completed task settles."""

    @pytest.fixture
    def wired_tool(self, monkeypatch):
        import importlib

        import deerflow.tools.builtins as builtins_pkg

        # `import deerflow.tools.builtins.task_tool as tt` binds the *parent
        # attribute* — which is the re-exported StructuredTool, not the module
        # (the package's `from .task_tool import task_tool` shadows it). Go
        # through the import system explicitly.
        tt = importlib.import_module("deerflow.tools.builtins.task_tool")
        assert hasattr(tt, "get_stream_writer"), "module shadowed again"
        del builtins_pkg

        captured: list[dict] = []

        class _Writer:
            def __call__(self, event):
                captured.append(event)

        monkeypatch.setattr(tt, "get_stream_writer", lambda: _Writer())

        # execute_async is deliberately SYNC (it hands work to a thread pool
        # and returns the task id); a coroutine here would itself be the bug.
        monkeypatch.setattr(
            tt.SubagentExecutor,
            "execute_async",
            lambda self, prompt, task_id=None: task_id or "tc-1",
        )

        # The RESULT dataclass must come from the live executor module (it may
        # have been importlib.reload()ed by other suites), but the STATUS must
        # come from task_tool's own import — that is the object its
        # `result.status == SubagentStatus.COMPLETED` branch compares against.
        import deerflow.subagents.executor as executor_module

        done = executor_module.SubagentResult(
            status=tt.SubagentStatus.COMPLETED,
            result="done",
            task_id="tc-1",
            trace_id="tr",
        )
        monkeypatch.setattr(tt, "get_background_task_result", lambda tid: done)
        monkeypatch.setattr(tt, "cleanup_background_task", lambda tid: None)

        # Guard against an endless poll loop: two sleeps means the terminal
        # state was never observed.
        sleeps: list[float] = []

        async def guarded_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 2:
                raise AssertionError(f"poll loop stuck: events={captured}")

        monkeypatch.setattr(tt.asyncio, "sleep", guarded_sleep)

        runtime = SimpleNamespace(
            state={
                "todos": [
                    {"content": "only", "status": "in_progress"},
                ],
                "sandbox": {},
                "thread_data": None,
            },
            context={"thread_id": "t-9"},
            # Carry a parent model_name the way a real runtime always does, so
            # the subagent inherits it instead of falling through to
            # get_app_config() — which has no models under the CI
            # config.example.yaml and would raise (this test mocks the actual
            # subagent execution, so the model is never invoked).
            config={
                "configurable": {"thread_id": "t-9"},
                "metadata": {"model_name": "test-model"},
            },
        )
        return tt, runtime, captured

    @pytest.mark.asyncio
    async def test_started_and_completed_carry_todo_indexes(self, wired_tool) -> None:
        tt, runtime, captured = wired_tool
        await tt.task_tool.coroutine(
            runtime=runtime,
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            tool_call_id="tc-1",
        )
        types = [(e["type"], e.get("todo_indexes")) for e in captured]
        assert ("task_started", [0]) in types
        assert ("task_completed", [0]) in types
