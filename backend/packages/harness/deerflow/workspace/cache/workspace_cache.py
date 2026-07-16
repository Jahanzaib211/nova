"""Workspace Cache — persistent cache for workspace snapshots.

Phase C9 — caches workspace graphs, symbol indexes, and dependency
graphs on disk.  The cache is invalidated when any indexed file
changes.  Uses a 10k entry ring buffer (LRU) for in-memory index.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from deerflow.workspace.cache.cache_key import CacheKeyBuilder
from deerflow.workspace.graph.command_registry import CommandRegistry
from deerflow.workspace.graph.dependency_graph import DependencyGraph
from deerflow.workspace.graph.symbol_index import SymbolIndex
from deerflow.workspace.graph.workspace_graph import WorkspaceGraph
from deerflow.workspace.models.workspace_snapshot import WorkspaceSnapshot


@dataclass
class CacheEntry:
    key: str
    created_at: float
    last_accessed: float
    hit_count: int
    snapshot_json: str


@dataclass
class WorkspaceCache:
    """Persistent cache for workspace snapshots.

    Stores cached WorkspaceSnapshots on disk as JSON.  Maintains an
    in-memory LRU index (10k max entries) for fast lookups.
    Cache directory: ``{DEER_FLOW_HOME}/.deer-flow/workspace_cache/``
    """

    cache_dir: Path
    _key_builder: CacheKeyBuilder = CacheKeyBuilder()
    _memory_index: dict[str, CacheEntry] | None = None
    _max_entries: int = 10000

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_index = {}
        self._load_index()

    def _cache_file_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _load_index(self) -> None:
        idx_path = self.cache_dir / "index.json"
        if idx_path.exists():
            try:
                data = json.loads(idx_path.read_text())
                for k, v in data.items():
                    self._memory_index[k] = CacheEntry(**v)
            except Exception:
                pass

    def _save_index(self) -> None:
        idx_path = self.cache_dir / "index.json"
        data = {k: asdict(v) for k, v in self._memory_index.items()}
        idx_path.write_text(json.dumps(data, sort_keys=True))

    def get(self, key: str) -> WorkspaceSnapshot | None:
        """Retrieve a cached snapshot by key."""
        if key not in self._memory_index:
            return None

        path = self._cache_file_path(key)
        if not path.exists():
            return None

        entry = self._memory_index[key]
        entry.last_accessed = time.time()
        entry.hit_count += 1

        try:
            data = json.loads(path.read_text())
            return WorkspaceSnapshot.from_dict(data)
        except Exception:
            return None

    def set(self, key: str, snapshot: WorkspaceSnapshot) -> None:
        """Store a snapshot in the cache."""
        if len(self._memory_index) >= self._max_entries:
            self._evict_lru()

        path = self._cache_file_path(key)
        path.write_text(json.dumps(snapshot.to_dict(), sort_keys=True))

        entry = CacheEntry(
            key=key,
            created_at=time.time(),
            last_accessed=time.time(),
            hit_count=0,
            snapshot_json="",
        )
        self._memory_index[key] = entry
        self._save_index()

    def _evict_lru(self) -> None:
        if not self._memory_index:
            return
        lru_key = min(self._memory_index, key=lambda k: self._memory_index[k].last_accessed)
        entry = self._memory_index.pop(lru_key, None)
        if entry:
            self._cache_file_path(lru_key).unlink(missing_ok=True)

    def invalidate(self, key: str) -> None:
        """Remove a specific cache entry."""
        self._memory_index.pop(key, None)
        self._cache_file_path(key).unlink(missing_ok=True)
        self._save_index()

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        for key in list(self._memory_index.keys()):
            self._cache_file_path(key).unlink(missing_ok=True)
        self._memory_index.clear()
        self._save_index()

    def build_key(
        self,
        root_path: str,
        repo_kind: str,
        primary_language: str,
        project_count: int,
        file_count: int,
    ) -> str:
        """Build a cache key from workspace metadata."""
        return self._key_builder.build_snapshot_key(
            root_path, repo_kind, primary_language, project_count, file_count
        )

    def size(self) -> int:
        """Number of entries in cache."""
        return len(self._memory_index)

    def stats(self) -> dict:
        """Return cache statistics."""
        if not self._memory_index:
            return {"entries": 0, "total_hits": 0}
        return {
            "entries": len(self._memory_index),
            "total_hits": sum(e.hit_count for e in self._memory_index.values()),
        }
