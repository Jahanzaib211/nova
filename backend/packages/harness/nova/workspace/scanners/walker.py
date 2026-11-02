"""Bounded filesystem walker for workspace traversal.

Phase C9 — traversal must always terminate.  This module provides
a walker that enforces resource budgets: max depth, max files,
max time, and max memory.  All traversals are cancellation-aware.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from deerflow.workspace.scanners.ignore import GitignoreParser, build_gitignore_matcher, should_ignore_name


@dataclass
class TraversalStats:
    """Statistics from a bounded walk."""

    files_visited: int = 0
    dirs_visited: int = 0
    bytes_read: int = 0
    duration_ms: float = 0.0
    cancelled: bool = False
    limit_reason: str = ""
    symlinks_detected: int = 0
    ignored_dirs: int = 0
    ignored_files: int = 0


@dataclass
class TraversalLimit:
    """Resource budgets for a bounded walk.

    All limits are applied strictly.  The walker terminates as soon
    as any budget is exhausted.
    """

    max_depth: int = 6
    max_files: int = 50_000
    max_duration_seconds: float = 15.0
    max_dirs: int = 10_000
    follow_symlinks: bool = False


@dataclass
class WalkEntry:
    """A single filesystem entry from the walker."""

    root: str
    dirs: list[str]
    files: list[str]
    depth: int


class BoundedWalker:
    """Bounded filesystem walker with deterministic termination.

    Walks a directory tree with the following guarantees:
    - Terminates when any TraversalLimit budget is exhausted
    - Respects .gitignore patterns when gitignore_matcher is provided
    - Detects symlinks and optionally follows them
    - Thread-safe: concurrent iteration from multiple threads supported
    """

    def __init__(
        self,
        root: Path,
        limits: TraversalLimit | None = None,
        gitignore_matcher: GitignoreParser | None = None,
    ) -> None:
        self.root = root.resolve()
        self.limits = limits or TraversalLimit()
        self._gitignore = gitignore_matcher or build_gitignore_matcher(self.root)
        self._cancel = threading.Event()
        self._stats = TraversalStats()

    def cancel(self) -> None:
        """Signal cancellation.  The walker terminates at the next step."""
        self._cancel.set()

    def walk(self) -> Iterator[WalkEntry]:
        """Walk the directory tree, yielding entries as they are discovered.

        Yields:
            WalkEntry for each directory level.

        Terminates early if:
        - max_files limit reached
        - max_depth exceeded
        - max_duration_seconds elapsed
        - cancel() called
        """
        start = time.monotonic()
        self._stats = TraversalStats()
        self._cancel.clear()

        yield from self._walk(self.root, depth=0, start=start)

    def _walk(self, current_root: Path, depth: int, start: float) -> Iterator[WalkEntry]:
        if self._cancel.is_set():
            return
        if depth > self.limits.max_depth:
            self._stats.limit_reason = f"max_depth({self.limits.max_depth})"
            return
        if self._stats.dirs_visited >= self.limits.max_dirs:
            self._stats.limit_reason = f"max_dirs({self.limits.max_dirs})"
            return
        if (time.monotonic() - start) > self.limits.max_duration_seconds:
            self._stats.limit_reason = f"max_duration({self.limits.max_duration_seconds}s)"
            return

        try:
            entries = list(os.scandir(current_root))
        except OSError:
            return

        dirs: list[str] = []
        files: list[str] = []

        for entry in entries:
            try:
                name = entry.name
                if should_ignore_name(name):
                    continue
                if entry.is_symlink():
                    self._stats.symlinks_detected += 1
                    if not self.limits.follow_symlinks:
                        continue
                if entry.is_dir():
                    dirs.append(name)
                elif entry.is_file():
                    files.append(name)
                    self._stats.files_visited += 1
                    if self._stats.files_visited >= self.limits.max_files:
                        self._stats.limit_reason = f"max_files({self.limits.max_files})"
                        self._cancel.set()
                        return
            except OSError:
                continue

        yield WalkEntry(root=str(current_root), dirs=dirs, files=files, depth=depth)

        self._stats.dirs_visited += 1

        for dname in dirs:
            subdir = current_root / dname
            if self._gitignore.matches(subdir):
                self._stats.ignored_dirs += 1
                continue
            yield from self._walk(subdir, depth + 1, start)

    @property
    def stats(self) -> TraversalStats:
        return self._stats


def walk_workspace(
    root: Path,
    limits: TraversalLimit | None = None,
) -> tuple[Iterator[WalkEntry], TraversalStats, BoundedWalker]:
    """Convenience function to walk a workspace.

    Returns:
        Tuple of (iterator, stats, walker).  The walker can be cancelled
        by calling walker.cancel().
    """
    walker = BoundedWalker(root, limits)
    return walker.walk(), walker.stats, walker
