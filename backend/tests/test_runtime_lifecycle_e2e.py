"""HTTP/runtime lifecycle E2E tests for the Gateway-owned runs API.

These tests keep the external model out of scope while exercising the real
FastAPI app, auth middleware, lifespan-created runtime dependencies,
``start_run()``, ``run_agent()``, StreamBridge, checkpointer, run store, and
thread metadata store.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import queue
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from _agent_e2e_helpers import FakeToolCallingModel, build_single_tool_call_model
from langchain_core.messages import AIMessage, HumanMessage
from support.gateway_app import create_thread as _create_thread
from support.gateway_app import drain_stream as _drain_stream
from support.gateway_app import register_user as _register_user
from support.gateway_app import run_body as _run_body
from support.gateway_app import run_id_from_response as _run_id_from_response

pytestmark = pytest.mark.no_auto_user


class _RunController:
    """Cross-thread controls for the fake async agent."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.checkpoint_written = threading.Event()
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.instances: list[_ScriptedAgent] = []


class _ScriptedAgent:
    """Deterministic runtime double for lifecycle-only tests.

    This is intentionally not a full LangGraph graph. Tests that need
    controllable blocking, cancellation, and rollback checkpoints use the small
    ``run_agent`` surface they exercise: ``astream()``, checkpointer/store
    attachment, metadata, and interrupt node attributes. The real lead-agent
    graph/tool dispatch path is covered separately by
    ``test_stream_run_executes_real_lead_agent_setup_agent_business_path``.
    """

    def __init__(
        self,
        controller: _RunController,
        *,
        title: str,
        answer: str,
        block_after_first_chunk: bool = False,
    ) -> None:
        self.controller = controller
        self.title = title
        self.answer = answer
        self.block_after_first_chunk = block_after_first_chunk
        self.checkpointer: Any | None = None
        self.store: Any | None = None
        self.metadata = {"model_name": "fake-test-model"}
        self.interrupt_before_nodes = None
        self.interrupt_after_nodes = None
        self.model = FakeToolCallingModel(responses=[AIMessage(content=self.answer)])

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        del subgraphs
        self.controller.started.set()

        try:
            thread_id = _thread_id_from_config(config)
            human_text = _last_human_text(graph_input)
            human = HumanMessage(content=human_text)
            ai = await self.model.ainvoke([human], config=config)
            state = {"messages": [human.model_dump(), ai.model_dump()], "title": self.title}

            if self.checkpointer is not None:
                await _write_checkpoint(self.checkpointer, thread_id=thread_id, state=state)
            self.controller.checkpoint_written.set()

            yield _stream_item_for_mode(stream_mode, state)

            if self.block_after_first_chunk:
                while not self.controller.release.is_set():
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            # Catch cancellation arriving anywhere in the body — including the
            # `await ainvoke()` / `_write_checkpoint()` / `yield` points between
            # ``started.set()`` and the original inner ``try`` — so tests that
            # wait for ``cancelled`` after issuing ``POST /cancel`` no longer
            # race with cancellation arriving early.
            self.controller.cancelled.set()
            raise


def _make_agent_factory(controller: _RunController, **agent_kwargs):
    def factory(*, config):
        del config
        agent = _ScriptedAgent(controller, **agent_kwargs)
        controller.instances.append(agent)
        return agent

    return factory


def _build_fake_setup_agent_model(agent_name: str):
    """Patch target for lead_agent.agent.create_chat_model.

    The graph, tool registry, ToolNode dispatch, and setup_agent implementation
    remain production code; this fake only replaces the external LLM call.
    """

    def fake_create_chat_model(*args: Any, **kwargs: Any) -> FakeToolCallingModel:
        del args, kwargs
        return build_single_tool_call_model(
            tool_name="setup_agent",
            tool_args={
                "soul": f"# Runtime Business E2E\n\nAgent name: {agent_name}",
                "description": "runtime lifecycle business path",
            },
            tool_call_id="call_runtime_business_1",
            final_text=f"Created {agent_name} through the real setup_agent tool.",
        )

    return fake_create_chat_model


def _parse_sse(transcript: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_frame in transcript.split("\n\n"):
        frame = raw_frame.strip()
        if not frame or frame.startswith(":"):
            continue
        parsed: dict[str, Any] = {}
        for line in frame.splitlines():
            if line.startswith("event: "):
                parsed["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = line.removeprefix("data: ")
                parsed["data"] = json.loads(payload)
            elif line.startswith("id: "):
                parsed["id"] = line.removeprefix("id: ")
        if parsed:
            events.append(parsed)
    return events


def _wait_for_status(client, thread_id: str, run_id: str, status: str, *, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == status:
            return last
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach {status!r}; last={last!r}")


def _thread_id_from_config(config: dict | None) -> str:
    config = config or {}
    context = config.get("context") if isinstance(config.get("context"), dict) else {}
    configurable = config.get("configurable") if isinstance(config.get("configurable"), dict) else {}
    thread_id = context.get("thread_id") or configurable.get("thread_id")
    assert thread_id, f"runtime config did not contain thread_id: {config!r}"
    return str(thread_id)


def _last_human_text(graph_input: dict) -> str:
    messages = graph_input.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", last)
    if isinstance(content, str):
        return content
    return str(content)


async def _write_checkpoint(checkpointer: Any, *, thread_id: str, state: dict[str, Any]) -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = dict(state)
    checkpoint["channel_versions"] = {key: 1 for key in state}
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    metadata = {
        "source": "loop",
        "step": 1,
        "writes": {"scripted_agent": {"title": state.get("title"), "message_count": len(state.get("messages", []))}},
        "parents": {},
    }

    result = checkpointer.aput(config, checkpoint, metadata, {})
    if inspect.isawaitable(result):
        _ = await result


def _stream_item_for_mode(stream_mode: Any, state: dict[str, Any]) -> Any:
    if isinstance(stream_mode, list):
        # ``run_agent`` passes a list when multiple modes/subgraphs are active.
        return stream_mode[0], state
    return state


def test_stream_run_completes_and_persists_runtime_state(isolated_app):
    """A streaming run should traverse the real runtime and leave state behind."""
    from starlette.testclient import TestClient

    controller = _RunController()
    factory = _make_agent_factory(
        controller,
        title="Lifecycle E2E",
        answer="Lifecycle complete.",
    )

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=factory),
        TestClient(isolated_app) as client,
    ):
        csrf_token = _register_user(client)
        thread_id = _create_thread(client, csrf_token)

        with client.stream(
            "POST",
            f"/api/threads/{thread_id}/runs/stream",
            json=_run_body(),
            headers={"X-CSRF-Token": csrf_token},
        ) as response:
            assert response.status_code == 200, response.read().decode()
            run_id = _run_id_from_response(response)
            transcript = _drain_stream(response)

        events = _parse_sse(transcript)
        assert [event["event"] for event in events] == ["metadata", "values", "end"]
        assert events[0]["data"] == {"run_id": run_id, "thread_id": thread_id}
        assert events[1]["data"]["title"] == "Lifecycle E2E"
        assert events[1]["data"]["messages"][-1]["content"] == "Lifecycle complete."

        run = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "success"

        thread = client.get(f"/api/threads/{thread_id}")
        assert thread.status_code == 200, thread.text
        assert thread.json()["status"] == "idle"
        assert thread.json()["values"]["title"] == "Lifecycle E2E"

        messages = client.get(f"/api/threads/{thread_id}/runs/{run_id}/messages")
        assert messages.status_code == 200, messages.text
        message_events = messages.json()["data"]
        event_types = [row["event_type"] for row in message_events]
        assert "llm.human.input" in event_types
        assert "llm.ai.response" in event_types
        assert any(row["content"]["content"] == "Run lifecycle E2E prompt" for row in message_events if row["event_type"] == "llm.human.input")
        assert any(row["content"]["content"] == "Lifecycle complete." for row in message_events if row["event_type"] == "llm.ai.response")


def test_join_terminal_run_with_released_buffer_ends_immediately(isolated_app):
    """Joining a finished run whose stream buffer was cleaned up returns ``end``.

    The frontend's rejoin lifecycle joins runs it believes are active; when it
    races a run that finished longer than the cleanup delay ago,
    ``bridge.subscribe`` used to lazily recreate an empty stream and the
    client would hang on heartbeats with no terminating event.
    """
    from starlette.testclient import TestClient

    controller = _RunController()
    factory = _make_agent_factory(
        controller,
        title="Join Terminal",
        answer="Join terminal complete.",
    )

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=factory),
        TestClient(isolated_app) as client,
    ):
        csrf_token = _register_user(client)
        thread_id = _create_thread(client, csrf_token)

        with client.stream(
            "POST",
            f"/api/threads/{thread_id}/runs/stream",
            json=_run_body(),
            headers={"X-CSRF-Token": csrf_token},
        ) as response:
            assert response.status_code == 200, response.read().decode()
            run_id = _run_id_from_response(response)
            _drain_stream(response)

        run = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "success"

        # Simulate the retention window having elapsed (the worker schedules
        # bridge cleanup with delay=60s after publish_end).
        bridge = isolated_app.state.stream_bridge
        asyncio.run(bridge.cleanup(run_id))
        assert asyncio.run(bridge.has_run(run_id)) is False

        with client.stream(
            "GET",
            f"/api/threads/{thread_id}/runs/{run_id}/stream",
        ) as join:
            assert join.status_code == 200
            transcript = _drain_stream(join, timeout=5.0)

        events = _parse_sse(transcript)
        assert [event["event"] for event in events] == ["end"]


def test_stream_run_executes_real_lead_agent_setup_agent_business_path(isolated_app, isolated_deer_flow_home: Path):
    """A runtime stream should execute real lead-agent business code and tools."""
    from starlette.testclient import TestClient

    agent_name = "runtime-business-agent"

    with (
        patch(
            "nova.agents.lead_agent.agent.create_chat_model",
            new=_build_fake_setup_agent_model(agent_name),
        ),
        TestClient(isolated_app) as client,
    ):
        csrf_token = _register_user(client, email="business-e2e@example.com")
        auth_user_id = client.get("/api/v1/auth/me").json()["id"]
        thread_id = _create_thread(client, csrf_token)

        body = _run_body(
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": f"Create a custom agent named {agent_name}.",
                    }
                ]
            },
            context={
                "agent_name": agent_name,
                "is_bootstrap": True,
                "thinking_enabled": False,
                "is_plan_mode": False,
                "subagent_enabled": False,
            },
        )

        with client.stream(
            "POST",
            f"/api/threads/{thread_id}/runs/stream",
            json=body,
            headers={"X-CSRF-Token": csrf_token},
        ) as response:
            assert response.status_code == 200, response.read().decode()
            run_id = _run_id_from_response(response)
            transcript = _drain_stream(response, timeout=20.0)

        events = _parse_sse(transcript)
        event_names = [event["event"] for event in events]
        assert "metadata" in event_names
        assert "error" not in event_names, transcript
        assert event_names[-1] == "end"

        run = _wait_for_status(client, thread_id, run_id, "success", timeout=10.0)
        assert run["assistant_id"] == "lead_agent"

        expected_soul = isolated_deer_flow_home / "users" / auth_user_id / "agents" / agent_name / "SOUL.md"
        assert expected_soul.exists(), f"setup_agent did not write SOUL.md. tmp tree: {sorted(str(p.relative_to(isolated_deer_flow_home)) for p in isolated_deer_flow_home.rglob('SOUL.md'))}"
        assert f"Agent name: {agent_name}" in expected_soul.read_text(encoding="utf-8")
        assert not (isolated_deer_flow_home / "users" / "default" / "agents" / agent_name).exists()


def test_cancel_interrupt_stops_running_background_run(isolated_app):
    """HTTP cancel?action=interrupt should stop the worker and persist interruption."""
    from starlette.testclient import TestClient

    controller = _RunController()
    factory = _make_agent_factory(
        controller,
        title="Interrupt candidate",
        answer="This run should be interrupted.",
        block_after_first_chunk=True,
    )

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=factory),
        TestClient(isolated_app) as client,
    ):
        csrf_token = _register_user(client, email="interrupt-e2e@example.com")
        thread_id = _create_thread(client, csrf_token)

        created = client.post(
            f"/api/threads/{thread_id}/runs",
            json=_run_body(),
            headers={"X-CSRF-Token": csrf_token},
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        assert controller.started.wait(5), "fake agent never started"

        cancelled = client.post(
            f"/api/threads/{thread_id}/runs/{run_id}/cancel?wait=true&action=interrupt",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert cancelled.status_code == 204, cancelled.text
        assert controller.cancelled.wait(5), "fake agent task was not cancelled"

        run = _wait_for_status(client, thread_id, run_id, "interrupted")
        assert run["status"] == "interrupted"

        thread = client.get(f"/api/threads/{thread_id}")
        assert thread.status_code == 200, thread.text
        assert thread.json()["status"] == "idle"


@pytest.mark.anyio
async def test_sse_consumer_disconnect_cancels_inflight_run():
    """A disconnected SSE request should cancel an in-flight run when configured."""
    from app.gateway.services import sse_consumer
    from nova.runtime import DisconnectMode, MemoryStreamBridge, RunManager, RunStatus

    bridge = MemoryStreamBridge()
    run_manager = RunManager()
    record = await run_manager.create("thread-disconnect", on_disconnect=DisconnectMode.cancel)
    await run_manager.set_status(record.run_id, RunStatus.running)
    await bridge.publish(record.run_id, "metadata", {"run_id": record.run_id, "thread_id": record.thread_id})
    worker_started = asyncio.Event()
    worker_cancelled = asyncio.Event()

    async def _pending_worker() -> None:
        try:
            worker_started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    record.task = asyncio.create_task(_pending_worker())
    await asyncio.wait_for(worker_started.wait(), timeout=1.0)

    class _DisconnectedRequest:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return True

    try:
        frames = []
        async for frame in sse_consumer(bridge, record, _DisconnectedRequest(), run_manager):
            frames.append(frame)

        assert frames == []
        assert record.abort_event.is_set()
        assert record.status == RunStatus.interrupted
        await asyncio.wait_for(worker_cancelled.wait(), timeout=1.0)
        assert record.task.cancelled()
    finally:
        if record.task is not None and not record.task.done():
            record.task.cancel()
            with suppress(asyncio.CancelledError):
                await record.task


def test_cancel_rollback_restores_pre_run_checkpoint(isolated_app):
    """HTTP cancel?action=rollback should restore the checkpoint captured before run start."""
    from starlette.testclient import TestClient

    controller = _RunController()
    factory = _make_agent_factory(
        controller,
        title="During rollback run",
        answer="This answer should be rolled back.",
        block_after_first_chunk=True,
    )

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=factory),
        TestClient(isolated_app) as client,
    ):
        csrf_token = _register_user(client, email="rollback-e2e@example.com")
        thread_id = _create_thread(client, csrf_token)

        before = client.post(
            f"/api/threads/{thread_id}/state",
            json={
                "values": {
                    "title": "Before rollback",
                    "messages": [{"type": "human", "content": "before"}],
                },
                "as_node": "test_seed",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert before.status_code == 200, before.text
        assert before.json()["values"]["title"] == "Before rollback"

        created = client.post(
            f"/api/threads/{thread_id}/runs",
            json=_run_body(),
            headers={"X-CSRF-Token": csrf_token},
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        assert controller.checkpoint_written.wait(5), "fake agent did not write in-run checkpoint"

        during = client.get(f"/api/threads/{thread_id}/state")
        assert during.status_code == 200, during.text
        assert during.json()["values"]["title"] == "During rollback run"

        rolled_back = client.post(
            f"/api/threads/{thread_id}/runs/{run_id}/cancel?wait=true&action=rollback",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert rolled_back.status_code == 204, rolled_back.text
        assert controller.cancelled.wait(5), "rollback did not cancel the worker task"

        run = _wait_for_status(client, thread_id, run_id, "error")
        assert run["status"] == "error"

        after = client.get(f"/api/threads/{thread_id}/state")
        assert after.status_code == 200, after.text
        assert after.json()["values"]["title"] == "Before rollback"
        assert after.json()["values"]["messages"] == [{"type": "human", "content": "before"}]
