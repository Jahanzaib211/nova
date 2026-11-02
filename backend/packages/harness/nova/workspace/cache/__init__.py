"""Cache — persistent cache for workspace snapshots.

Phase C9 — caches workspace graphs, symbol indexes, and dependency
graphs on disk with TTL-based and content-hash invalidation.
"""

from __future__ import annotations

from nova.workspace.cache.cache_key import CacheKeyBuilder
from nova.workspace.cache.invalidator import CacheInvalidator
from nova.workspace.cache.workspace_cache import CacheEntry, WorkspaceCache

__all__ = [
    "CacheEntry",
    "CacheInvalidator",
    "CacheKeyBuilder",
    "WorkspaceCache",
]
