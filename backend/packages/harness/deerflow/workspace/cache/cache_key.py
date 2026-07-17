"""Cache Key Builder — deterministic cache keys for workspace snapshots.

Phase C9 — cache keys are content-addressed: the key is derived from
the workspace content (file hashes, project structure), not from
the query.  This ensures the same workspace state always maps to
the same cache entry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class CacheKeyBuilder:
    """Build deterministic cache keys for workspace states.

    Key is a full (untruncated — 2026-07 audit B6) SHA-256 of the
    canonical JSON representation of the
    workspace fingerprint (repo kind, languages, project count,
    file count).  File-level content hashing is available as a
    separate method for deep invalidation.
    """

    def build_snapshot_key(
        self,
        root_path: str,
        repo_kind: str,
        primary_language: str,
        project_count: int,
        file_count: int,
    ) -> str:
        """Build a cache key from workspace metadata."""
        payload = json.dumps(
            {
                "root": str(Path(root_path).resolve()),
                "kind": repo_kind,
                "language": primary_language,
                "projects": project_count,
                "files": file_count,
            },
            sort_keys=True,
        )
        return f"wik:{hashlib.sha256(payload.encode()).hexdigest()}"

    def build_file_hash_key(self, file_path: str, content_hash: str) -> str:
        """Build a cache key for a single file's content hash."""
        return f"wik:file:{hashlib.sha256(f"{file_path}:{content_hash}".encode()).hexdigest()}"

    def build_symbol_key(self, project_id: str, symbol_name: str) -> str:
        """Build a cache key for a symbol lookup result."""
        return f"wik:sym:{hashlib.sha256(f"{project_id}:{symbol_name}".encode()).hexdigest()}"
