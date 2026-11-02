"""Symbol Index — in-memory inverted index for symbol lookup.

Phase C9 — the symbol index replaces filesystem grep.
It maps symbol names to their definitions and references.
Queries are O(1) dict lookup + optional prefix scan.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from deerflow.workspace.models.symbol import Symbol


@dataclass
class SymbolIndex:
    """In-memory inverted index: symbol_name → list[Symbol].

    Thread-safe for concurrent reads.  Mutations require external locking.
    Supports exact match and prefix search.
    """

    _by_name: dict[str, list[Symbol]] = field(default_factory=lambda: defaultdict(list))
    _by_fqn: dict[str, Symbol] = field(default_factory=dict)
    _by_project: dict[str, list[Symbol]] = field(default_factory=lambda: defaultdict(list))

    def add(self, symbol: Symbol) -> None:
        """Register a symbol in the index."""
        self._by_name[symbol.name].append(symbol)
        self._by_fqn[symbol.fqn] = symbol
        if symbol.project_id:
            self._by_project[symbol.project_id].append(symbol)

    def add_many(self, symbols: list[Symbol]) -> None:
        """Register multiple symbols."""
        for s in symbols:
            self.add(s)

    def find_exact(self, name: str) -> list[Symbol]:
        """Return all symbols with exactly this name."""
        return list(self._by_name.get(name, []))

    def find_prefix(self, prefix: str) -> list[Symbol]:
        """Return all symbols whose name starts with ``prefix``."""
        results = []
        for name, syms in self._by_name.items():
            if name.startswith(prefix):
                results.extend(syms)
        return results

    def find_by_fqn(self, fqn: str) -> Symbol | None:
        """Return the symbol with this fully-qualified name."""
        return self._by_fqn.get(fqn)

    def find_by_project(self, project_id: str) -> list[Symbol]:
        """Return all symbols belonging to a project."""
        return list(self._by_project.get(project_id, []))

    def count(self) -> int:
        """Total number of unique symbol names."""
        return len(self._by_name)

    def symbol_count(self) -> int:
        """Total number of symbol instances."""
        return sum(len(v) for v in self._by_name.values())

    def merge(self, other: SymbolIndex) -> None:
        """Merge another index into this one."""
        for name, syms in other._by_name.items():
            for s in syms:
                self.add(s)
