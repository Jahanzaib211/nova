"""Workspace Snapshot — the complete indexed state of a workspace.

Phase C9 — a snapshot is the immutable result of a full workspace index.
It is cached and reused until invalidated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deerflow.workspace.models.command import Command
from deerflow.workspace.models.dependency import Dependency
from deerflow.workspace.models.fingerprint import RepositoryFingerprint
from deerflow.workspace.models.node import Edge, Node
from deerflow.workspace.models.project import Module, Package, Project
from deerflow.workspace.models.symbol import Symbol


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Complete indexed state of a workspace.

    Produced by ``WorkspaceKernel.index()``.  Immutable.  Cached.
    """

    thread_id: str
    fingerprint: RepositoryFingerprint
    projects: tuple[Project, ...] = field(default_factory=tuple)
    packages: tuple[Package, ...] = field(default_factory=tuple)
    modules: tuple[Module, ...] = field(default_factory=tuple)
    symbols: tuple[Symbol, ...] = field(default_factory=tuple)
    commands: tuple[Command, ...] = field(default_factory=tuple)
    dependencies: tuple[Dependency, ...] = field(default_factory=tuple)
    graph_nodes: tuple[Node, ...] = field(default_factory=tuple)
    graph_edges: tuple[Edge, ...] = field(default_factory=tuple)
    indexed_at: str = ""
    cache_key: str = ""
    traversal_count: int = 0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)

    @property
    def command_count(self) -> int:
        return len(self.commands)

    @property
    def node_count(self) -> int:
        return len(self.graph_nodes)

    @property
    def edge_count(self) -> int:
        return len(self.graph_edges)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict form (str-enum members serialize as their values)."""
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceSnapshot:
        """Rebuild a snapshot from :meth:`to_dict` output.

        JSON round-trips turn tuples into lists; each nested model's own
        ``__post_init__`` re-coerces enum strings.
        """
        from dataclasses import fields as dc_fields

        from deerflow.workspace.models.symbol import SymbolLocation

        def build(item_cls, payload: dict[str, Any]):
            names = {f.name for f in dc_fields(item_cls)}
            kwargs = {k: (tuple(v) if isinstance(v, list) else v) for k, v in payload.items() if k in names}
            return item_cls(**kwargs)

        def build_symbol(payload: dict[str, Any]) -> Symbol:
            locations = tuple(build(SymbolLocation, loc) for loc in payload.get("locations") or ())
            return build(Symbol, {**payload, "locations": locations})

        return cls(
            thread_id=data.get("thread_id", ""),
            fingerprint=build(RepositoryFingerprint, data.get("fingerprint") or {}),
            projects=tuple(build(Project, p) for p in data.get("projects") or ()),
            packages=tuple(build(Package, p) for p in data.get("packages") or ()),
            modules=tuple(build(Module, m) for m in data.get("modules") or ()),
            symbols=tuple(build_symbol(s) for s in data.get("symbols") or ()),
            commands=tuple(build(Command, c) for c in data.get("commands") or ()),
            dependencies=tuple(build(Dependency, d) for d in data.get("dependencies") or ()),
            graph_nodes=tuple(build(Node, n) for n in data.get("graph_nodes") or ()),
            graph_edges=tuple(build(Edge, e) for e in data.get("graph_edges") or ()),
            indexed_at=data.get("indexed_at", ""),
            cache_key=data.get("cache_key", ""),
            traversal_count=data.get("traversal_count", 0),
            duration_ms=data.get("duration_ms", 0.0),
            metadata=data.get("metadata") or {},
        )
