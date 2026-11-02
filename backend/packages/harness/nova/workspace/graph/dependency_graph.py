"""Dependency Graph — import/export dependency tracking.

Phase C9 — tracks which modules/files depend on which other modules.
Used for impact analysis (what breaks if I change X) and
for topological ordering of build/test steps.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from deerflow.workspace.models.dependency import Dependency, DepKind


@dataclass
class DependencyGraph:
    """Directed graph of import dependencies between files/modules.

    Supports: add, remove, reverse lookup (who depends on X),
    forward lookup (what does X depend on), and cycle detection.
    """

    _outgoing: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _incoming: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_dep(self, source_path: str, target_path: str, kind: DepKind = DepKind.RUNTIME) -> None:
        """Record that source imports/depends on target."""
        self._outgoing[source_path].add(target_path)
        self._incoming[target_path].add(source_path)

    def remove(self, path: str) -> None:
        """Remove a node and all its edges from the graph."""
        for target in list(self._outgoing.get(path, set())):
            self._incoming[target].discard(path)
        for source in list(self._incoming.get(path, set())):
            self._outgoing[source].discard(path)
        self._outgoing.pop(path, None)
        self._incoming.pop(path, None)

    def dependents_of(self, path: str) -> list[str]:
        """Return all paths that depend on ``path`` (reverse deps)."""
        return list(self._incoming.get(path, set()))

    def dependencies_of(self, path: str) -> list[str]:
        """Return all paths that ``path`` depends on."""
        return list(self._outgoing.get(path, set()))

    def topological_sort(self) -> list[str]:
        """Return a topological ordering of all paths (Kahn's algorithm)."""
        in_degree = defaultdict(int)
        all_nodes: set[str] = set()
        for source, targets in self._outgoing.items():
            all_nodes.add(source)
            for t in targets:
                all_nodes.add(t)
                in_degree[t] += 1
        for source in self._incoming:
            all_nodes.add(source)

        queue = [n for n in all_nodes if in_degree[n] == 0]
        result = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for target in list(self._outgoing.get(node, set())):
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)
        return result

    def find_cycles(self) -> list[list[str]]:
        """Return all simple cycles (each cycle is a list of paths)."""
        cycles = []
        visited: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> None:
            if node in stack:
                idx = stack.index(node)
                cycles.append(list(stack[idx:]))
                return
            if node in visited:
                return
            visited.add(node)
            stack.append(node)
            for dep in self._outgoing.get(node, set()):
                dfs(dep)
            stack.pop()

        for node in self._outgoing:
            if node not in visited:
                dfs(node)
        return cycles

    def affected_by(self, path: str) -> set[str]:
        """Return all paths transitively affected by a change to ``path``."""
        affected: set[str] = set()
        queue = [path]
        while queue:
            current = queue.pop()
            for dep in self._incoming.get(current, set()):
                if dep not in affected:
                    affected.add(dep)
                    queue.append(dep)
        return affected
