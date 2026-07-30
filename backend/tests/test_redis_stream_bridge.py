"""Tests for RedisStreamBridge — the cross-replica StreamBridge implementation.

Mirrors test_stream_bridge.py's MemoryStreamBridge coverage so both
implementations are proven to satisfy the same behavioral contract. Uses
fakeredis (an in-memory Redis-protocol-compatible server) rather than a
real Redis instance — no external infra needed to run this suite.
"""

import re

import fakeredis
import pytest

from deerflow.config.app_config import AppConfig
from deerflow.config.stream_bridge_config import StreamBridgeConfig
from deerflow.runtime.stream_bridge import END_SENTINEL, HEARTBEAT_SENTINEL, RedisStreamBridge, make_stream_bridge

# ---------------------------------------------------------------------------
# Unit tests for RedisStreamBridge
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge() -> RedisStreamBridge:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    return RedisStreamBridge(client=client, queue_maxsize=256)


@pytest.mark.anyio
async def test_publish_subscribe(bridge: RedisStreamBridge):
    """Three events followed by end should be received in order."""
    run_id = "run-1"

    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"messages": []})
    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(received) == 4
    assert received[0].event == "metadata"
    assert received[0].data == {"run_id": run_id}
    assert received[1].event == "values"
    assert received[2].event == "updates"
    assert received[3] is END_SENTINEL


@pytest.mark.anyio
async def test_heartbeat(bridge: RedisStreamBridge):
    """When no events arrive within the heartbeat interval, yield a heartbeat."""
    run_id = "run-heartbeat"

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        received.append(entry)
        if entry is HEARTBEAT_SENTINEL:
            break

    assert len(received) == 1
    assert received[0] is HEARTBEAT_SENTINEL


@pytest.mark.anyio
async def test_cleanup_immediate(bridge: RedisStreamBridge):
    """cleanup(delay=0) deletes the stream key immediately."""
    run_id = "run-cleanup"
    await bridge.publish(run_id, "test", {})
    assert await bridge.has_run(run_id) is True

    await bridge.cleanup(run_id)
    assert await bridge.has_run(run_id) is False


@pytest.mark.anyio
async def test_cleanup_with_delay_sets_ttl(bridge: RedisStreamBridge):
    """cleanup(delay>0) sets a TTL rather than deleting immediately — the
    stream must still exist right after the call (grace window for late
    subscribers), unlike MemoryStreamBridge's local-sleep approach."""
    run_id = "run-cleanup-delayed"
    await bridge.publish(run_id, "test", {})

    await bridge.cleanup(run_id, delay=60)
    assert await bridge.has_run(run_id) is True
    ttl = await bridge._redis.ttl(bridge._key(run_id))
    assert 0 < ttl <= 60


@pytest.mark.anyio
async def test_has_run_tracks_retention(bridge: RedisStreamBridge):
    """has_run is True while the stream key exists and False after cleanup."""
    run_id = "run-retention"
    assert await bridge.has_run(run_id) is False

    await bridge.publish(run_id, "test", {})
    assert await bridge.has_run(run_id) is True

    await bridge.publish_end(run_id)
    assert await bridge.has_run(run_id) is True

    await bridge.cleanup(run_id)
    assert await bridge.has_run(run_id) is False


@pytest.mark.anyio
async def test_multiple_runs(bridge: RedisStreamBridge):
    """Two different run_ids should not interfere with each other."""
    await bridge.publish("run-a", "event-a", {"a": 1})
    await bridge.publish("run-b", "event-b", {"b": 2})
    await bridge.publish_end("run-a")
    await bridge.publish_end("run-b")

    events_a = []
    async for entry in bridge.subscribe("run-a", heartbeat_interval=1.0):
        events_a.append(entry)
        if entry is END_SENTINEL:
            break

    events_b = []
    async for entry in bridge.subscribe("run-b", heartbeat_interval=1.0):
        events_b.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events_a) == 2
    assert events_a[0].event == "event-a"
    assert events_a[0].data == {"a": 1}

    assert len(events_b) == 2
    assert events_b[0].event == "event-b"
    assert events_b[0].data == {"b": 2}


@pytest.mark.anyio
async def test_event_id_format(bridge: RedisStreamBridge):
    """Event IDs use Redis's native ms-seq stream ID format."""
    run_id = "run-id-format"
    await bridge.publish(run_id, "test", {"key": "value"})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    event = received[0]
    assert re.match(r"^\d+-\d+$", event.id), f"Expected ms-seq format, got {event.id}"


@pytest.mark.anyio
async def test_subscribe_replays_after_last_event_id(bridge: RedisStreamBridge):
    """Reconnect should replay events strictly after the provided Last-Event-ID."""
    run_id = "run-replay"
    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"step": 1})
    await bridge.publish(run_id, "updates", {"step": 2})
    await bridge.publish_end(run_id)

    first_pass = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        first_pass.append(entry)
        if entry is END_SENTINEL:
            break

    received = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=first_pass[0].id,
        heartbeat_interval=1.0,
    ):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["values", "updates"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_publish_end_without_history_yields_end_immediately(bridge: RedisStreamBridge):
    """Subscribers should still receive END when a run completes without events."""
    run_id = "run-end-empty"
    await bridge.publish_end(run_id)

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events) == 1
    assert events[0] is END_SENTINEL


@pytest.mark.anyio
async def test_late_subscriber_after_run_finished(bridge: RedisStreamBridge):
    """A subscriber that attaches after publish_end already happened still
    sees the full history plus END — proves cross-replica join works."""
    run_id = "run-late-join"
    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    await bridge.publish_end(run_id)

    # Simulates a different replica joining after the producer replica finished.
    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert [e.event for e in events[:-1]] == ["event-1", "event-2"]
    assert events[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_maxlen_bounds_history(bridge: RedisStreamBridge):
    """Retained history should be approximately bounded by queue_maxsize."""
    small_bridge = RedisStreamBridge(client=fakeredis.FakeAsyncRedis(decode_responses=True), queue_maxsize=2)
    run_id = "run-bounded"
    for i in range(20):
        await small_bridge.publish(run_id, f"event-{i}", {"i": i})
    await small_bridge.publish_end(run_id)

    length = await small_bridge._redis.xlen(small_bridge._key(run_id))
    # MAXLEN ~ is approximate (Redis trims in whole macro-nodes for
    # performance) — assert it's bounded to a small multiple of maxsize,
    # not exactly maxsize.
    assert length <= 10


# ---------------------------------------------------------------------------
# Import-hygiene test — redis stays an optional extra
# ---------------------------------------------------------------------------


def test_import_hygiene_no_top_level_redis_import():
    """redis_provider.py must not import redis at module load time — it's
    an optional extra (pip install 'deerflow-harness[redis]'), and the
    'memory' stream bridge type must keep working without it installed."""
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).parent.parent
        / "packages"
        / "harness"
        / "deerflow"
        / "runtime"
        / "stream_bridge"
        / "redis_provider.py"
    )
    tree = ast.parse(source_path.read_text())
    top_level_imports = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "redis" not in top_level_imports


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_make_stream_bridge_redis_requires_url():
    """The redis stream bridge type without a redis_url raises a clear error."""
    config = AppConfig.model_construct(stream_bridge=StreamBridgeConfig(type="redis", redis_url=None))
    with pytest.raises(ValueError, match="redis_url"):
        async with make_stream_bridge(config):
            pass


@pytest.mark.anyio
async def test_make_stream_bridge_redis_yields_redis_bridge(monkeypatch: pytest.MonkeyPatch):
    """With a redis_url configured, the factory yields a RedisStreamBridge."""
    fake_client = fakeredis.FakeAsyncRedis(decode_responses=True)

    def fake_from_url(url, decode_responses=True, socket_timeout=None):
        assert url == "redis://example-test-host:6379/0"
        return fake_client

    import redis.asyncio

    monkeypatch.setattr(redis.asyncio, "from_url", fake_from_url)

    config = AppConfig.model_construct(
        stream_bridge=StreamBridgeConfig(type="redis", redis_url="redis://example-test-host:6379/0")
    )
    async with make_stream_bridge(config) as bridge:
        assert isinstance(bridge, RedisStreamBridge)
        # Prove it's actually wired to a working client, not just the right type.
        await bridge.publish("smoke-test", "e", {})
        assert await bridge.has_run("smoke-test") is True
