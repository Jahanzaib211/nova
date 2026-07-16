"""Workspace Graph — the complete typed graph of the workspace.

Phase C9 — the workspace graph represents all entities in the workspace
as typed nodes and directed edges.  It is built from the scanner results
and parser results, and is queryable for impact analysis and planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deerflow.workspace.models.node import Edge, EdgeKind, Node, NodeKind
from deerflow.workspace.models.project import Project


@dataclass
class WorkspaceGraph:
    """The complete workspace graph.

    Nodes represent filesystem entities and abstract concepts.
    Edges represent relationships between nodes.

    Built incrementally from scan + parse results.
    Queryable by node kind, edge kind, and path.
    """

    _nodes_by_id: dict[str, Node] = field(default_factory=dict)
    _nodes_by_path: dict[str, str] = field(default_factory=dict)
    _edges: list[Edge] = field(default_factory=list)
    _adjacency: dict[str, list[str]] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        """Add a node to the graph."""
        self._nodes_by_id[node.node_id] = node
        if node.path:
            self._nodes_by_path[node.path] = node.node_id
        self._adjacency.setdefault(node.node_id, [])

    def add_edge(self, edge: Edge) -> None:
        """Add a directed edge to the graph."""
        self._edges.append(edge)
        self._adjacency.setdefault(edge.source_id, []).append(edge.target_id)

    def get_node(self, node_id: str) -> Node | None:
        """Return a node by its id."""
        return self._nodes_by_id.get(node_id)

    def get_node_by_path(self, path: str) -> Node | None:
        """Return a node by its file path."""
        node_id = self._nodes_by_path.get(path)
        return self._nodes_by_id.get(node_id) if node_id else None

    def nodes(self, kind: NodeKind | None = None) -> list[Node]:
        """Return all nodes, optionally filtered by kind."""
        if kind is None:
            return list(self._nodes_by_id.values())
        return [n for n in self._nodes_by_id.values() if n.kind == kind]

    def edges(self) -> list[Edge]:
        """Return all edges."""
        return list(self._edges)

    def outgoing_edges(self, node_id: str) -> list[Edge]:
        """Return all edges originating from ``node_id``."""
        return [e for e in self._edges if e.source_id == node_id]

    def incoming_edges(self, node_id: str) -> list[Edge]:
        """Return all edges targeting ``node_id``."""
        return [e for e in self._edges if e.target_id == node_id]

    def find_projects(self) -> list[Node]:
        """Return all project nodes."""
        return self.nodes(NodeKind.PROJECT)

    def find_files(self, pattern: str = "") -> list[Node]:
        """Return file nodes, optionally filtered by name pattern."""
        files = self.nodes(NodeKind.FILE)
        if pattern:
            pattern_lower = pattern.lower()
            files = [f for f in files if pattern_lower in f.label.lower()]
        return files

    def build_from_projects(self, projects: list[Project]) -> None:
        """Build the project subgraph from a list of projects."""
        for project in projects:
            node = Node(
                node_id=project.project_id,
                kind=NodeKind.PROJECT,
                label=project.name,
                path=project.root_path,
                project_id=project.project_id,
                metadata={"kind": project.kind.value, "is_runnable": project.is_runnable, "has_tests": project.has_tests},
            )
            self.add_node(node)

    def node_count(self) -> int:
        return len(self._nodes_by_id)

    def edge_count(self) -> int:
        return len(self._edges)
