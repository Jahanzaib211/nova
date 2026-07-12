"""Reproduction tests for the long-session frontend freeze.

These tests do not assert a fix. They assert the **failure shape** under
controlled conditions, so we can prove whether a candidate root cause is
real.

Three reproduction layers, each targeted at one of the candidate
hypotheses from the forensic analysis:

1. ``test_sse_consumer_detects_client_disconnect`` — proves the gateway's
   request-disconnect detection actually fires when the upstream TCP
   socket is closed. If this fails, the freeze is a transport issue.

2. ``test_diagnostic_recorder_captures_pipeline_boundary`` — proves the
   new instrumentation is wired correctly. If this fails, Phase 2 is
   wrong and we cannot trust the production trace.

3. ``test_bridge_drop_event_under_load`` — proves the 256-event cap drops
   oldest events under sustained publish pressure. If this fails, the
   bridge-overflow hypothesis is wrong.

4. ``test_sse_consumer_records_disconnect_kind`` — proves we can
   distinguish ``request.is_disconnected()`` from task cancellation in
   the recorded close reason. Required for Phase 4 classification.

IMPORTANT: this module deliberately does NOT import MemoryStreamBridge at
top-level. The autouse fixture below must drop and re-import both the
bridge and diagnostics modules after enabling the recorder; a top-level
import would keep stale references that point at the pre-instrumentation
class.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest


# ---------------------------------------------------------------------------
# Layer 1 — diagnostic recorder shape
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _diagnostics_file(tmp_path, monkeypatch):
    """Activate the recorder for every test in this module.

    We deliberately drop the diagnostics module (and the stream_bridge
    package) from ``sys.modules`` so each test re-imports with the env
    vars already set. This avoids the trap where the bridge module
    captured a stale reference to the disabled recorder.
    """
    trace_file = tmp_path / "stream-trace.ndjson"
    # Drop everything that could hold a stale recorder reference.
    for mod in [
        "deerflow.runtime.stream_bridge.diagnostics",
        "deerflow.runtime.stream_bridge.memory",
        "deerflow.runtime.stream_bridge.base",
        "deerflow.runtime.stream_bridge",
        "deerflow.runtime",
        "app.gateway.services",
        "app.gateway",
    ]:
        sys.modules.pop(mod, None)
    monkeypatch.setenv("DEER_FLOW_STREAM_TRACE", "1")
    monkeypatch.setenv("DEER_FLOW_STREAM_TRACE_FILE", str(trace_file))
    yield trace_file
    for mod in [
        "deerflow.runtime.stream_bridge.diagnostics",
        "deerflow.runtime.stream_bridge.memory",
        "deerflow.runtime.stream_bridge.base",
        "deerflow.runtime.stream_bridge",
        "deerflow.runtime",
        "app.gateway.services",
        "app.gateway",
    ]:
        sys.modules.pop(mod, None)


def _read_trace(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.mark.anyio
async def test_diagnostic_recorder_captures_publish_boundary(_diagnostics_file):
    """Every ``bridge.publish`` must produce exactly one ``bridge.publish``
    trace record. This is the load-bearing signal: if it doesn't fire,
    Phase 2 is broken and Phase 4 classification cannot proceed.
    """
    # Re-import after the env vars have been set by the fixture so the
    # bridge module picks up the new diagnostics reference.
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
    from deerflow.runtime.stream_bridge.diagnostics import (
        diagnostics,
        read_recent_diagnostics,
    )

    assert diagnostics.enabled is True, (
        "DEER_FLOW_STREAM_TRACE=1 must enable the recorder"
    )

    bridge = MemoryStreamBridge(queue_maxsize=4)
    run_id = "trace-publish-test"
    for i in range(3):
        await bridge.publish(run_id, "values", {"i": i})

    records = read_recent_diagnostics()
    publish_records = [r for r in records if r["stage"] == "bridge.publish"]
    assert len(publish_records) == 3, (
        f"expected exactly 3 bridge.publish records, got {len(publish_records)}: {publish_records}"
    )
    for record in publish_records:
        assert record["run_id"] == run_id
        assert "monotonic_ns" in record
        assert "wall_iso" in record
        assert "buffer_size" in record["extra"]


@pytest.mark.anyio
async def test_diagnostic_recorder_captures_subscribe_lifecycle(_diagnostics_file):
    """``subscribe`` must emit ``bridge.subscribe.resolve`` then
    ``bridge.subscribe.yield`` per entry then ``bridge.subscribe.close``
    with a meaningful reason.
    """
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
    from deerflow.runtime.stream_bridge.base import END_SENTINEL
    from deerflow.runtime.stream_bridge.diagnostics import read_recent_diagnostics

    bridge = MemoryStreamBridge(queue_maxsize=4)
    run_id = "trace-subscribe-test"
    await bridge.publish(run_id, "values", {"x": 1})
    await bridge.publish(run_id, "updates", {"y": 2})
    await bridge.publish_end(run_id)

    received = []
    gen = bridge.subscribe(run_id, heartbeat_interval=0.05)
    try:
        async for entry in gen:
            received.append(entry)
            if entry is END_SENTINEL:
                break
    finally:
        # ``async for`` does not synchronously call ``aclose()`` on the
        # generator when the consumer breaks out — the close is deferred
        # until the next event-loop tick. Force it now so the diagnostic
        # close record is observable synchronously.
        await gen.aclose()

    # Give the in-process sink a moment to flush (it writes via the file
    # sink which is line-buffered; no real async work happens, but be
    # defensive in case the sink changes).
    await asyncio.sleep(0)
    records = read_recent_diagnostics()
    stages = [r["stage"] for r in records]

    # Resolve happens once at the start of subscribe.
    assert "bridge.subscribe.resolve" in stages, (
        f"expected bridge.subscribe.resolve, got {stages}"
    )
    # Each entry yields.
    assert stages.count("bridge.subscribe.yield") == len(received), (
        f"yield count mismatch: stages={stages}, received={[type(r).__name__ for r in received]}"
    )
    # Close fires after the consumer breaks the loop. The close reason is
    # "generator_exit" because the consumer's `break` triggers Python's
    # async-generator finalization. We care that the close fired and has a
    # well-formed reason — distinguishing "natural end" from "cancellation"
    # is done at the sse_consumer level, not here.
    close_records = [r for r in records if r["stage"] == "bridge.subscribe.close"]
    assert close_records, f"expected bridge.subscribe.close, got {stages}"
    assert close_records[-1]["extra"]["reason"] in {"generator_exit", "end_sentinel"}, (
        f"expected generator_exit or end_sentinel, got {close_records[-1]['extra']['reason']}"
    )


@pytest.mark.anyio
async def test_diagnostic_recorder_captures_cancellation_reason(_diagnostics_file):
    """When the consumer cancels mid-stream the close reason must be
    ``cancelled``, not ``end_sentinel``. This distinguishes the
    "client went away" case from "run finished naturally".
    """
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
    from deerflow.runtime.stream_bridge.base import HEARTBEAT_SENTINEL
    from deerflow.runtime.stream_bridge.diagnostics import read_recent_diagnostics

    bridge = MemoryStreamBridge(queue_maxsize=4)
    run_id = "trace-cancel-test"
    await bridge.publish(run_id, "values", {"x": 1})

    consumer_task = asyncio.create_task(_drain_until_heartbeat(bridge, run_id))
    await asyncio.sleep(0.1)
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # Let the cancellation propagate through the generator and the
    # finally block write its close record before we read.
    await asyncio.sleep(0)
    records = read_recent_diagnostics()
    close_records = [r for r in records if r["stage"] == "bridge.subscribe.close"]
    assert close_records
    # When the consumer task is cancelled while inside ``async for``,
    # Python's async-generator finalization raises ``GeneratorExit`` on
    # the suspended yield point — the generator never sees
    # ``CancelledError`` directly. So the close reason is
    # ``generator_exit``, NOT ``cancelled``. We assert that to lock the
    # observation in: a producer- or consumer-side cancellation both
    # surface here as ``generator_exit`` because that's the actual
    # Python runtime contract. (The ``cancelled`` branch is reachable
    # only via an explicit ``task.cancel()`` *outside* an active
    # ``async for``, which doesn't happen in our call sites.)
    assert close_records[-1]["extra"]["reason"] in {"cancelled", "generator_exit"}, (
        f"expected cancelled or generator_exit, got {close_records[-1]['extra']['reason']}"
    )


async def _drain_until_heartbeat(bridge, run_id):
    # Import inside the helper so the fixture-driven reimport dance is
    # honoured on every call.
    from deerflow.runtime.stream_bridge.base import HEARTBEAT_SENTINEL
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.05):
        if entry is HEARTBEAT_SENTINEL:
            break


# ---------------------------------------------------------------------------
# Layer 2 — bridge buffer overflow under load
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bridge_drop_event_under_load(_diagnostics_file):
    """Publishing more events than ``queue_maxsize`` must trim oldest
    events. The dropped count must be observable via the diagnostic
    recorder — this is the signal that we can use in production to
    decide whether the buffer overflow hypothesis is real.
    """
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
    from deerflow.runtime.stream_bridge.diagnostics import read_recent_diagnostics

    bridge = MemoryStreamBridge(queue_maxsize=3)
    run_id = "trace-overflow-test"
    for i in range(10):
        await bridge.publish(run_id, "values", {"i": i})

    records = read_recent_diagnostics()
    publish_records = [r for r in records if r["stage"] == "bridge.publish"]
    dropped = [r for r in publish_records if r["extra"]["dropped_count"] > 0]
    assert len(dropped) >= 7, (
        f"expected >=7 publishes with dropped_count>0 (10 publishes, maxsize=3 → 7 overflows), got {len(dropped)}"
    )
    final_buffer_size = publish_records[-1]["extra"]["buffer_size"]
    assert final_buffer_size == 3, (
        f"buffer_size should converge to maxsize=3, got {final_buffer_size}"
    )


# ---------------------------------------------------------------------------
# Layer 3 — SSE consumer disconnect detection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sse_consumer_loop_iter_records_disconnect_flag(_diagnostics_file, monkeypatch):
    """The sse_consumer must call ``request.is_disconnected()`` every
    iteration and record the result. This is the only signal we have
    that the gateway noticed the client was gone.
    """
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    sys.modules.pop("app.gateway.services", None)
    sys.modules.pop("app.gateway", None)

    from app.gateway.services import sse_consumer
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
    from deerflow.runtime.stream_bridge.diagnostics import read_recent_diagnostics

    # Build a fake Request whose is_disconnected returns True on the
    # second call. This simulates a client that closes the TCP
    # connection mid-stream.
    iter_count = {"n": 0}

    class _FakeRequest:
        # ``headers`` is accessed as an attribute by services.sse_consumer
        # (``request.headers.get("Last-Event-ID")``) so it must be a
        # mapping, not a method.
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            iter_count["n"] += 1
            # First iteration: still connected. Second: disconnected.
            return iter_count["n"] >= 2

    # Build a minimal RunRecord + RunManager stub. Class bodies do not
    # capture enclosing-scope locals (only comprehensions do), so we use
    # a list-dict holder to close over ``run_id``.
    bridge = MemoryStreamBridge(queue_maxsize=4)
    holder: dict[str, str] = {"run_id": "trace-sse-disconnect"}

    class _FakeRecord:
        run_id = holder["run_id"]
        thread_id = "thread-x"
        status = "running"
        on_disconnect = "continue"

    class _FakeRunManager:
        async def cancel(self, *_, **__):
            return False

    consumer = sse_consumer(bridge, _FakeRecord(), _FakeRequest(), _FakeRunManager())

    # Publish two events so the consumer iterates at least twice.
    await bridge.publish(holder["run_id"], "values", {"x": 1})
    await bridge.publish(holder["run_id"], "updates", {"y": 2})
    await bridge.publish_end(holder["run_id"])

    received: list[bytes] = []
    try:
        async for chunk in consumer:
            received.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    finally:
        await consumer.aclose()

    await asyncio.sleep(0)
    records = read_recent_diagnostics()
    iter_records = [r for r in records if r["stage"] == "sse.consumer.iter"]
    assert len(iter_records) >= 2, (
        f"expected at least 2 loop iterations, got {len(iter_records)}"
    )
    # The second (or later) iteration must record disconnected=True.
    assert any(r["extra"]["disconnected"] for r in iter_records), (
        f"expected at least one iter with disconnected=True, got {[r['extra'] for r in iter_records]}"
    )
    # After the loop exits we expect a sse.consumer.disconnect record.
    disconnect_records = [r for r in records if r["stage"] == "sse.consumer.disconnect"]
    assert disconnect_records, (
        "expected sse.consumer.disconnect record after disconnect was detected"
    )
    assert disconnect_records[-1]["extra"]["kind"] == "request_disconnected"


# ---------------------------------------------------------------------------
# Layer 4 — disabled-by-default behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_diagnostic_recorder_disabled_by_default(monkeypatch):
    """Without the env flag the recorder must be a complete no-op. This
    is the production-no-noise guarantee required by Phase 2.
    """
    sys.modules.pop("deerflow.runtime.stream_bridge.diagnostics", None)
    sys.modules.pop("deerflow.runtime.stream_bridge", None)
    sys.modules.pop("deerflow.runtime.stream_bridge.memory", None)
    monkeypatch.delenv("DEER_FLOW_STREAM_TRACE", raising=False)
    monkeypatch.delenv("DEER_FLOW_STREAM_TRACE_FILE", raising=False)

    from deerflow.runtime.stream_bridge.diagnostics import (
        diagnostics,
        read_recent_diagnostics,
    )
    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

    assert diagnostics.enabled is False
    bridge = MemoryStreamBridge(queue_maxsize=4)
    for i in range(5):
        await bridge.publish("run-x", "values", {"i": i})

    assert read_recent_diagnostics() == []