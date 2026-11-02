"""Scanners — bounded filesystem traversal.

Phase C9 — all filesystem traversal goes through the BoundedWalker
which guarantees deterministic termination.
"""

from __future__ import annotations

from deerflow.workspace.scanners.ignore import GitignoreParser, build_gitignore_matcher, should_ignore_name, should_ignore_path
from deerflow.workspace.scanners.walker import BoundedWalker, TraversalLimit, TraversalStats, WalkEntry, walk_workspace

__all__ = [
    "GitignoreParser",
    "build_gitignore_matcher",
    "should_ignore_name",
    "should_ignore_path",
    "BoundedWalker",
    "TraversalLimit",
    "TraversalStats",
    "WalkEntry",
    "walk_workspace",
]
