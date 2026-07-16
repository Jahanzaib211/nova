"""Ignore patterns for bounded filesystem traversal.

Phase C9 — traversal must exclude build artifacts, caches, dependencies,
vendor directories, and system files.  This module provides a comprehensive
ignore list plus gitignore-aware filtering.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

IGNORE_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "env",
        ".tox",
        ".nox",
        ".eggs",
        "site-packages",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".output",
        ".turbo",
        "target",
        "out",
        ".idea",
        ".vscode",
        ".project",
        ".classpath",
        ".settings",
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "coverage",
        ".coverage",
        ".nyc_output",
        "htmlcov",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "logs",
        ".parcel-cache",
        ".vite",
        ".svelte-kit",
        ".wrangler",
        ".workers",
        ".serverless",
        ".funcignore",
    }
)


_GLOB_PATTERNS = tuple(p for p in IGNORE_NAMES if "*" in p or "?" in p)
_EXACT_IGNORE = frozenset(p for p in IGNORE_NAMES if "*" not in p and "?" not in p)


def should_ignore_name(name: str) -> bool:
    """Return True if a filename or directory name should be ignored."""
    if name in _EXACT_IGNORE:
        return True
    name_lower = name.lower()
    for pattern in _GLOB_PATTERNS:
        if fnmatch.fnmatch(name_lower, pattern.lower()):
            return True
    return False


def should_ignore_path(path: Path) -> bool:
    """Return True if any component of a path should be ignored."""
    for part in path.parts:
        if should_ignore_name(part):
            return True
    return False


class GitignoreParser:
    """Parses .gitignore files and builds a matcher.

    Supports the common .gitignore patterns:
    - Exact names (node_modules)
    - Glob patterns (*.pyc, *.log)
    - Directory patterns (build/, dist/)
    - Negation patterns (!src/)
    """

    def __init__(self) -> None:
        self._exclude_rules: list[re.Pattern] = []
        self._include_rules: list[re.Pattern] = []

    def add_file(self, path: Path) -> None:
        """Load patterns from a .gitignore file."""
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            pattern = self._gitignore_pattern_to_regex(line)
            compiled = re.compile(pattern, re.IGNORECASE)
            if negate:
                self._include_rules.append(compiled)
            else:
                self._exclude_rules.append(compiled)

    def matches(self, path: Path) -> bool:
        """Return True if the path matches any exclusion rule."""
        if not self._exclude_rules and not self._include_rules:
            return False
        path_str = str(path)
        matched_exclude = any(r.search(path_str) for r in self._exclude_rules)
        matched_include = any(r.search(path_str) for r in self._include_rules)
        return matched_exclude and not matched_include

    def _gitignore_pattern_to_regex(self, pattern: str) -> str:
        if pattern.endswith("/"):
            pattern = pattern[:-1]
            dir_only = True
        else:
            dir_only = False
        pattern = re.escape(pattern)
        pattern = pattern.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]?")
        if dir_only:
            return f"(?:{pattern}(?:/.*)?)"
        return f"(?:{pattern}(?:/.*)?)"


def build_gitignore_matcher(root: Path) -> GitignoreParser:
    """Build a gitignore matcher for a directory tree."""
    matcher = GitignoreParser()
    cursor = root
    while cursor != cursor.parent:
        gitignore = cursor / ".gitignore"
        matcher.add_file(gitignore)
        try:
            cursor = cursor.parent
        except ValueError:
            break
    return matcher
