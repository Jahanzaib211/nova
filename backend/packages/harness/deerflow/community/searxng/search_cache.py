"""Thread-safe LRU cache with TTL for search results.

Provides deterministic caching keyed by (query, categories, language, pageno).
When the same query is repeated within the TTL window, the cached result is
returned without hitting the network — guaranteeing identical output for
identical inputs.

Configuration (env vars):
    DEERFLOW_IGINO_CACHE_TTL_S       default 300 (5 minutes)
    DEERFLOW_IGINO_CACHE_MAX_SIZE    default 1024
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

_TTL_S = float(os.environ.get("DEERFLOW_IGINO_CACHE_TTL_S", "300"))
_MAX_SIZE = int(os.environ.get("DEERFLOW_IGINO_CACHE_MAX_SIZE", "1024"))


@dataclass(frozen=True)
class CacheKey:
    query: str
    categories: str
    language: str
    pageno: int

    def to_hash(self) -> str:
        raw = json.dumps(
            {"q": self.query, "c": self.categories, "l": self.language, "p": self.pageno},
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class _CacheEntry:
    key_hash: str
    value: list[dict[str, Any]]
    inserted_at: float
    access_count: int = 0
    last_accessed: float = 0.0

    def is_expired(self, now: float, ttl: float) -> bool:
        return (now - self.inserted_at) > ttl


class SearchCache:
    """LRU+TTL cache for search results.

    Thread-safe via a single lock. Entries are evicted in LRU order when the
    cache exceeds max_size. Expired entries are lazily pruned on access.
    """

    def __init__(self, ttl_s: float = _TTL_S, max_size: int = _MAX_SIZE) -> None:
        self._ttl = ttl_s
        self._max_size = max_size
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: CacheKey) -> list[dict[str, Any]] | None:
        key_hash = key.to_hash()
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key_hash)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired(now, self._ttl):
                del self._entries[key_hash]
                self._misses += 1
                return None
            entry.access_count += 1
            entry.last_accessed = now
            self._hits += 1
            return list(entry.value)

    def put(self, key: CacheKey, value: list[dict[str, Any]]) -> None:
        key_hash = key.to_hash()
        now = time.monotonic()
        with self._lock:
            if key_hash in self._entries:
                self._entries[key_hash].value = list(value)
                self._entries[key_hash].inserted_at = now
                self._entries[key_hash].access_count += 1
                self._entries[key_hash].last_accessed = now
                return
            if len(self._entries) >= self._max_size:
                self._evict_lru()
            self._entries[key_hash] = _CacheEntry(
                key_hash=key_hash,
                value=list(value),
                inserted_at=now,
                access_count=1,
                last_accessed=now,
            )

    def _evict_lru(self) -> None:
        if not self._entries:
            return
        now = time.monotonic()
        expired = [h for h, e in self._entries.items() if e.is_expired(now, self._ttl)]
        for h in expired:
            del self._entries[h]
        if len(self._entries) >= self._max_size:
            oldest_hash = min(self._entries, key=lambda h: self._entries[h].last_accessed)
            del self._entries[oldest_hash]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._entries),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
                "ttl_s": self._ttl,
            }


_cache: SearchCache | None = None
_cache_lock = threading.Lock()


def get_search_cache() -> SearchCache:
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is None:
            _cache = SearchCache()
        return _cache


def reset_search_cache() -> None:
    global _cache
    with _cache_lock:
        if _cache is not None:
            _cache.clear()
        _cache = None
