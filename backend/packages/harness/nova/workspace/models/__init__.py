"""Domain models for the Workspace Intelligence Kernel.

Phase C9 — frozen dataclasses representing workspace entities.
All models are immutable.  Use ``replace()`` to create modified copies.
"""

from __future__ import annotations

from nova.workspace.models.command import Command, CommandKind
from nova.workspace.models.dependency import Dependency, DepKind
from nova.workspace.models.execution_plan import ExecutionPlan, ExecutionStep, RiskLevel
from nova.workspace.models.fingerprint import RepoKind, RepositoryFingerprint
from nova.workspace.models.node import Edge, Node, NodeKind
from nova.workspace.models.project import Module, Package, PackageKind, Project, ProjectKind
from nova.workspace.models.symbol import Symbol, SymbolKind, SymbolLocation
from nova.workspace.models.workspace_snapshot import WorkspaceSnapshot

__all__ = [
    "NodeKind",
    "Node",
    "Edge",
    "RepoKind",
    "RepositoryFingerprint",
    "ProjectKind",
    "PackageKind",
    "Project",
    "Package",
    "Module",
    "SymbolKind",
    "Symbol",
    "SymbolLocation",
    "CommandKind",
    "Command",
    "DepKind",
    "Dependency",
    "RiskLevel",
    "ExecutionStep",
    "ExecutionPlan",
    "WorkspaceSnapshot",
]
