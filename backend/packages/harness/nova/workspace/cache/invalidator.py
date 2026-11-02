"""Cache Invalidator — invalidates workspace cache on file changes.

Phase C9 — watches for file changes and invalidates stale cache
entries.  Integrates with the workspace snapshot TTL and with
inotify/FSEvents for live invalidation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from deerflow.workspace.cache.workspace_cache import WorkspaceCache


@dataclass
class CacheInvalidator:
    """Invalidate workspace cache entries based on file modification times.

    Strategy:
    - TTL-based: entries older than max_age_seconds are invalidated on next access
    - File-change-based: if any indexed file is newer than the cache entry, invalidate
    - Manual: explicit invalidate() calls
    """

    cache: WorkspaceCache
    max_age_seconds: float = 3600.0
    _last_check: float = field(default_factory=time.time)

    def is_fresh(self, cache_key: str, indexed_mtimes: dict[str, float]) -> bool:
        """Check if a cache entry is still fresh given current file mtimes.

        Returns True if all indexed files are older than the cache entry
        and the cache entry is within max_age_seconds.
        """
        entry_path = self.cache._cache_file_path(cache_key)
        if not entry_path.exists():
            return False

        cache_mtime = entry_path.stat().st_mtime
        now = time.time()
        if now - cache_mtime > self.max_age_seconds:
            return False

        for file_path, mtime in indexed_mtimes.items():
            if mtime > cache_mtime:
                return False

        return True

    def invalidate_stale(self) -> int:
        """Invalidate all stale entries based on max age.

        Returns the number of entries invalidated.
        """
        count = 0
        for key in list(self.cache._memory_index.keys()):
            entry = self.cache._memory_index.get(key)
            if entry and (time.time() - entry.created_at) > self.max_age_seconds:
                self.cache.invalidate(key)
                count += 1
        return count

    def invalidate_modified(self, file_path: str, mtime: float) -> None:
        """Invalidate cache entries affected by a file modification."""
        self.cache.invalidate_all()

    def check_and_invalidate(self, cache_key: str, indexed_mtimes: dict[str, float]) -> bool:
        """Check freshness and invalidate if stale.

        Returns True if the entry was valid (not invalidated).
        """
        if not self.is_fresh(cache_key, indexed_mtimes):
            self.cache.invalidate(cache_key)
            return False
        return True
