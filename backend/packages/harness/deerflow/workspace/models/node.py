"""Node and Edge models for the Workspace Graph.

Phase C9 — the workspace is represented as a typed directed graph.
Nodes are filesystem entities or abstract concepts.
Edges express relationships between nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """Kind of a workspace graph node."""

    # Filesystem entities
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"

    # Project entities
    PROJECT = "project"
    PACKAGE = "package"
    MODULE = "module"

    # Symbol entities
    SYMBOL = "symbol"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE = "type"
    INTERFACE = "interface"
    ENUM = "enum"

    # Command entities
    COMMAND = "command"
    ENTRY_POINT = "entry_point"

    # Dependency entities
    DEPENDENCY = "dependency"
    RUNTIME_DEP = "runtime_dep"
    BUILD_DEP = "build_dep"
    DEV_DEP = "dev_dep"

    # Service entities
    SERVICE = "service"
    WORKER = "worker"
    TASK = "task"

    # Deployment entities
    DOCKERFILE = "dockerfile"
    COMPOSE_SERVICE = "compose_service"
    PM2_PROCESS = "pm2_process"
    SYSTEMD_UNIT = "systemd_unit"

    # Documentation
    DOC = "doc"
    README = "readme"
    CHANGELOG = "changelog"

    # Unknown
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Node:
    """A node in the workspace graph.

    Nodes are identified by a stable ``node_id`` that is unique within
    a workspace snapshot.  Equality is by identity (two snapshots may
    contain nodes with the same ``node_id`` representing the same entity).
    """

    node_id: str
    kind: NodeKind
    label: str
    path: str = ""
    project_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", NodeKind(self.kind))


class EdgeKind(str, Enum):
    """Kind of a directed edge in the workspace graph."""

    CONTAINS = "contains"
    IMPORTS = "imports"
    EXPORTS = "exports"
    RUNS = "runs"
    BUILDS = "builds"
    DEPLOYS = "deploys"
    REQUIRES = "requires"
    IMPLEMENTS = "implements"
    EXTENDS = "extends"
    DEPENDS_ON = "depends_on"
    HAS_SYMBOL = "has_symbol"
    DEFINED_IN = "defined_in"
    CALLED_BY = "called_by"
    TESTED_BY = "tested_by"
    DOCUMENTED_IN = "documented_in"


@dataclass(frozen=True)
class Edge:
    """A directed edge in the workspace graph."""

    source_id: str
    target_id: str
    kind: EdgeKind
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EdgeKind(self.kind))
