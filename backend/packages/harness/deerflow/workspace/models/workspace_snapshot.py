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
