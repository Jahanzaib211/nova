"""Tests for cross-replica cancellation (CancelSignal).

The core scenario under test: a run owned by "replica A" (in ``_runs`` on
manager_a) gets a cancel request that arrives at "replica B" (which only
sees the run via the shared store, store_only=True). Replica B's cancel()
call must wake up replica A's local abort_event through the shared Redis
channel — this is the actual fix for the duplicate-delivery-class bug
found in this session's architecture audit (see k8s/ARCHITECTURE.md §4).

Uses fakeredis with a shared FakeServer so two separate client instances
behave exactly like two processes talking to one real Redis instance.
"""

import asyncio

import fakeredis
import pytest

from deerflow.runtime.runs import NoopCancelSignal, RedisCancelSignal, RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


@pytest.mark.anyio
async def test_noop_cancel_signal_request_is_harmless():
    """NoopCancelSignal.request_cancel is a no-op — matches today's
    single-replica behavior exactly when no Redis is configured."""
    signal = NoopCancelSignal()
    await signal.request_cancel("some-run")  # must not raise


@pytest.mark.anyio
async def test_noop_cancel_signal_wait_never_resolves_until_cancelled():
    """wait_for_cancel() on the noop signal only ever ends via external cancellation."""
    signal = NoopCancelSignal()
    task = asyncio.create_task(signal.wait_for_cancel("some-run"))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_redis_cancel_signal_request_then_wait():
    """Basic pub/sub round trip: a waiter started before the publish sees it."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    signal = RedisCancelSignal(client=client)

    waiter = asyncio.create_task(signal.wait_for_cancel("run-1"))
    await asyncio.sleep(0.05)  # let the subscribe() land before publishing
    await signal.request_cancel("run-1")

    await asyncio.wait_for(waiter, timeout=2.0)  # must resolve, not hang
    await client.aclose()


@pytest.mark.anyio
async def test_redis_cancel_signal_does_not_leak_across_run_ids():
    """A cancel for run-a must not wake a waiter subscribed to run-b."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    signal = RedisCancelSignal(client=client)

    waiter_b = asyncio.create_task(signal.wait_for_cancel("run-b"))
    await asyncio.sleep(0.05)
    await signal.request_cancel("run-a")

    await asyncio.sleep(0.1)
    assert not waiter_b.done()
    waiter_b.cancel()
    await client.aclose()


@pytest.mark.anyio
async def test_cross_replica_cancellation_end_to_end():
    """The actual scenario this feature exists for: two RunManager
    instances (simulating two Gateway replicas) share a store and a Redis
    cancel signal. A run started on manager_a can be cancelled via
    manager_b, and manager_a's local abort_event actually gets set —
    proving the owning replica's run_agent() loop would actually stop.
    """
    shared_store = MemoryRunStore()
    server = fakeredis.FakeServer()

    signal_a = RedisCancelSignal(client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    signal_b = RedisCancelSignal(client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True))

    manager_a = RunManager(store=shared_store, cancel_signal=signal_a)
    manager_b = RunManager(store=shared_store, cancel_signal=signal_b)

    # Replica A creates and "owns" the run — real in-memory record, real abort_event.
    record = await manager_a.create(
        thread_id="thread-1",
        assistant_id="lead_agent",
        kwargs={},
    )
    assert record.store_only is False

    # Simulate services.py's start_run wiring: a background watcher bridges
    # the remote signal into the local abort_event.
    watcher = manager_a.start_remote_cancel_watcher(record.run_id, record.abort_event)
    # Let the watcher's pubsub.subscribe() actually register before anyone
    # publishes — asyncio.create_task only schedules the coroutine, it
    # doesn't run it synchronously. In production this window is a
    # non-issue (the watcher starts once at run creation, long before a
    # user could click "stop"); in this test, without the yield, the
    # publish below can race ahead of the subscribe and be lost, which is
    # real Redis pub/sub semantics (no delivery to a not-yet-subscribed
    # listener), not a bug in this test working around a flake.
    await asyncio.sleep(0.05)

    # Replica B only sees this run via the shared store (store_only path) —
    # confirm it does NOT have it in its own in-memory dict.
    assert record.run_id not in manager_b._runs

    assert not record.abort_event.is_set()

    cancelled = await manager_b.cancel(record.run_id)
    assert cancelled is True

    # The whole point: replica A's local abort_event — the thing
    # run_agent()'s streaming loop actually polls — must flip, even though
    # the cancel request was handled entirely on replica B.
    await asyncio.wait_for(_wait_until_set(record.abort_event), timeout=2.0)
    assert record.abort_event.is_set()

    # And the durable store record reflects it too, matching today's
    # existing store-only cancel behavior.
    row = await shared_store.get(record.run_id)
    assert row["status"] == RunStatus.interrupted.value

    watcher.cancel()
    await signal_a.close()
    await signal_b.close()


async def _wait_until_set(event: asyncio.Event) -> None:
    while not event.is_set():
        await asyncio.sleep(0.01)
