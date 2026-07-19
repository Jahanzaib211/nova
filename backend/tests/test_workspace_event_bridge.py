"""Real, no-mock tests for WorkspaceEventBridge.

Exercises actual threading (a real background thread publishing through
the real synchronous EventBus, exactly as WorkspaceIntelligenceServiceImpl.
scan_async does via asyncio.to_thread), real asyncio.Queue backpressure,
and real root-path scoping. No mocks, no monkeypatched bus.
"""

import asyncio
import threading

import pytest

from deerflow.events.bus import EventBus
from deerflow.workspace.events import CacheHit, WorkspaceScanned
from deerflow.workspace.events.bridge import WorkspaceEventBridge


class TestSubscriptionScoping:
    @pytest.mark.anyio
    async def test_only_matching_root_path_is_delivered(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue = bridge.subscribe("/root/a")

        bus.publish(WorkspaceScanned(root_path="/root/b", file_count=1))
        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=2))

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.root_path == "/root/a"
        assert event.file_count == 2
        assert queue.empty()

    @pytest.mark.anyio
    async def test_multiple_subscribers_each_get_their_own_events(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue_a = bridge.subscribe("/root/a")
        _, queue_b = bridge.subscribe("/root/b")

        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=10))
        bus.publish(WorkspaceScanned(root_path="/root/b", file_count=20))

        event_a = await asyncio.wait_for(queue_a.get(), timeout=1.0)
        event_b = await asyncio.wait_for(queue_b.get(), timeout=1.0)
        assert event_a.file_count == 10
        assert event_b.file_count == 20

    @pytest.mark.anyio
    async def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        sub_id, queue = bridge.subscribe("/root/a")
        bridge.unsubscribe(sub_id)

        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=1))
        await asyncio.sleep(0.05)
        assert queue.empty()

    @pytest.mark.anyio
    async def test_different_event_types_both_pass_through(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue = bridge.subscribe("/root/a")

        bus.publish(WorkspaceScanned(root_path="/root/a"))
        bus.publish(CacheHit(root_path="/root/a", cache_key="k", hit_count=1))

        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        second = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(first, WorkspaceScanned)
        assert isinstance(second, CacheHit)


class TestCrossThreadDelivery:
    @pytest.mark.anyio
    async def test_publish_from_a_real_background_thread_is_delivered(self):
        """Mirrors scan_async's asyncio.to_thread — publish happens off-loop."""
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue = bridge.subscribe("/root/worker")

        def publish_from_worker_thread():
            bus.publish(WorkspaceScanned(root_path="/root/worker", file_count=99))

        thread = threading.Thread(target=publish_from_worker_thread)
        thread.start()
        thread.join()

        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event.file_count == 99

    @pytest.mark.anyio
    async def test_publish_via_asyncio_to_thread_is_delivered(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue = bridge.subscribe("/root/x")

        def blocking_scan_simulation():
            bus.publish(WorkspaceScanned(root_path="/root/x", symbol_count=42))

        await asyncio.to_thread(blocking_scan_simulation)
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event.symbol_count == 42


class TestBackpressure:
    @pytest.mark.anyio
    async def test_full_queue_drops_oldest_not_newest(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        _, queue = bridge.subscribe("/root/a", queue_maxsize=2)

        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=1))
        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=2))
        bus.publish(WorkspaceScanned(root_path="/root/a", file_count=3))
        await asyncio.sleep(0.05)

        assert queue.qsize() == 2
        first = await queue.get()
        second = await queue.get()
        assert (first.file_count, second.file_count) == (2, 3)

    @pytest.mark.anyio
    async def test_publisher_never_blocks_on_a_full_queue(self):
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)
        bridge.subscribe("/root/a", queue_maxsize=1)

        for i in range(50):
            bus.publish(WorkspaceScanned(root_path="/root/a", file_count=i))
        # If publish ever blocked on a full queue this would hang the test
        # (bus.publish is synchronous and has no timeout).


class TestDeadLoopIsSilentlyDropped:
    def test_publish_after_subscriber_loop_is_closed_does_not_raise(self):
        """A closed event loop (client disconnected, generator torn down)
        must not crash the publisher thread — call_soon_threadsafe on a
        closed loop raises RuntimeError, which the bridge must swallow."""
        bus = EventBus()
        bridge = WorkspaceEventBridge(bus)

        loop = asyncio.new_event_loop()
        loop.run_until_complete(asyncio.sleep(0))  # prime it
        # Manually construct the subscription against a loop we control,
        # bypassing subscribe()'s get_running_loop() (no loop is running
        # in this sync test — that's the point: the loop is dead/foreign).
        sub_id = "manual"
        queue: asyncio.Queue = asyncio.Queue()
        from deerflow.workspace.events.bridge import _Subscription

        bridge._subs[sub_id] = _Subscription(root_path="/root/a", loop=loop, queue=queue)
        loop.close()

        # Must not raise, even though the target loop is closed.
        bus.publish(WorkspaceScanned(root_path="/root/a"))
