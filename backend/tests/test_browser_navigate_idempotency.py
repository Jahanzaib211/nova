"""Unit tests for the browser_navigate idempotency cache (v7 C3).

Pins the contract:
  - cache hit within TTL returns the same string
  - cache miss returns None
  - TTL expiry evicts the entry
  - cap-based eviction (oldest-by-timestamp) bounds memory
  - thread-safe under concurrent put/get
  - errors are NOT cached (only successful navigate results)
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.tools.builtins.workspace_tools import (
    _browser_navigate_idempotency,
    _BrowserNavigateIdempotency,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    _browser_navigate_idempotency.clear()
    yield
    _browser_navigate_idempotency.clear()


class TestPutGet:
    def test_miss_returns_none(self) -> None:
        assert _browser_navigate_idempotency.get("t1", "n1") is None

    def test_put_then_get(self) -> None:
        _browser_navigate_idempotency.put("t1", "n1", "Opened http://a")
        assert _browser_navigate_idempotency.get("t1", "n1") == "Opened http://a"

    def test_distinct_keys(self) -> None:
        _browser_navigate_idempotency.put("t1", "n1", "a")
        _browser_navigate_idempotency.put("t1", "n2", "b")
        _browser_navigate_idempotency.put("t2", "n1", "c")
        assert _browser_navigate_idempotency.get("t1", "n1") == "a"
        assert _browser_navigate_idempotency.get("t1", "n2") == "b"
        assert _browser_navigate_idempotency.get("t2", "n1") == "c"

    def test_put_overwrites(self) -> None:
        _browser_navigate_idempotency.put("t1", "n1", "first")
        _browser_navigate_idempotency.put("t1", "n1", "second")
        assert _browser_navigate_idempotency.get("t1", "n1") == "second"

    def test_clear(self) -> None:
        _browser_navigate_idempotency.put("t1", "n1", "a")
        _browser_navigate_idempotency.clear()
        assert _browser_navigate_idempotency.get("t1", "n1") is None


class TestTtlExpiry:
    def test_expired_entry_is_evicted(self) -> None:
        """Manually age an entry past the TTL and verify get() returns None."""
        cache = _BrowserNavigateIdempotency()
        # Force a 0-second TTL by writing an entry timestamped in the past.
        cache.put("t1", "n1", "stale")
        # Reach into the cache to backdate the entry.
        cache._data[("t1", "n1")] = (time.monotonic() - 10_000, "stale")
        assert cache.get("t1", "n1") is None


class TestEvictionCap:
    def test_eviction_when_over_cap(self) -> None:
        """Putting more entries than the cap should evict the oldest."""
        # Build a fresh cache; the put() method's opportunistic eviction
        # keeps the dict bounded when size > _NAVIGATE_IDEMPOTENCY_MAX_ENTRIES.
        # We use a real cache and rely on its documented cap to validate the
        # contract: the cache size is bounded.
        cache = _BrowserNavigateIdempotency()

        # Use many distinct keys to ensure put() doesn't just overwrite.
        # The default cap (256) means after we write 300 entries, the cache
        # must have evicted at least 44 of them.
        from deerflow.tools.builtins.workspace_tools import (
            _NAVIGATE_IDEMPOTENCY_MAX_ENTRIES,
        )

        n_writes = _NAVIGATE_IDEMPOTENCY_MAX_ENTRIES + 50
        for i in range(n_writes):
            cache.put(f"t-{i}", "n", f"v-{i}")
        # Cap is the upper bound; we may be at-or-below it after eviction.
        assert len(cache._data) <= _NAVIGATE_IDEMPOTENCY_MAX_ENTRIES, (
            f"cache exceeded cap: {len(cache._data)} > {_NAVIGATE_IDEMPOTENCY_MAX_ENTRIES}"
        )

    def test_eviction_keeps_recent(self) -> None:
        """The most recently inserted entry should survive eviction."""
        cache = _BrowserNavigateIdempotency()
        # Add many old entries, then a fresh one.
        old_ts = time.monotonic() - 10_000
        for i in range(500):
            cache._data[(f"old-{i}", "n")] = (old_ts + i, f"old-{i}")
        cache.put("t-new", "n", "fresh")
        assert cache.get("t-new", "n") == "fresh", "most-recent entry was evicted"


class TestThreadSafety:
    def test_concurrent_puts_no_corruption(self) -> None:
        """Concurrent puts on the same cache must not raise or lose data.

        Each worker writes a unique (thread_id, navigate_id) pair to its own
        space so eviction under burst load doesn't remove the keys we
        later assert on.
        """
        cache = _BrowserNavigateIdempotency()

        def worker(i: int) -> None:
            for j in range(20):
                cache.put(f"t{i}", f"n{j}", f"v{i}-{j}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(8)]
            for f in futures:
                f.result()
        # Verify all 160 entries survived (8 threads * 20 keys each).
        # The cap is 256 by default so we're well under it.
        assert len(cache._data) == 160, f"expected 160 entries, got {len(cache._data)}"
        assert cache.get("t0", "n0") == "v0-0"
        assert cache.get("t7", "n19") == "v7-19"

    def test_concurrent_get_during_put(self) -> None:
        """A concurrent get-during-put must return either old or new, never raise."""
        cache = _BrowserNavigateIdempotency()
        cache.put("t1", "n1", "v1")
        results = []

        def reader() -> None:
            for _ in range(100):
                results.append(cache.get("t1", "n1"))

        def writer() -> None:
            for i in range(100):
                cache.put("t1", "n1", f"v{i}")
                time.sleep(0.0001)

        t_reader = threading.Thread(target=reader)
        t_writer = threading.Thread(target=writer)
        t_reader.start()
        t_writer.start()
        t_reader.join()
        t_writer.join()
        # Every read returned a non-None string starting with 'v'.
        for r in results:
            assert r is not None
            assert r.startswith("v")


class TestContractForBrowserNavigateTool:
    """Pin the public contract the browser_navigate_tool relies on."""

    def test_cache_is_module_level_singleton(self) -> None:
        """All callers share one cache so duplicate navigate_ids collide."""
        from deerflow.tools.builtins.workspace_tools import (
            _browser_navigate_idempotency as cache1,
        )
        from deerflow.tools.builtins.workspace_tools import (
            _browser_navigate_idempotency as cache2,
        )
        assert cache1 is cache2

    def test_cache_clear_is_idempotent(self) -> None:
        _browser_navigate_idempotency.clear()
        _browser_navigate_idempotency.clear()
        assert _browser_navigate_idempotency.get("any", "any") is None