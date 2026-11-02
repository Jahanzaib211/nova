"""Dependency models — runtime, build, and dev dependencies.

Phase C9 — dependency graphs power impact analysis and incremental builds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DepKind(str, Enum):
    """Kind of dependency."""

    RUNTIME = "runtime"
    BUILD = "build"
    DEV = "dev"
    PEER = "peer"
    OPTIONAL = "optional"
    TRANSITIVE = "transitive"
    SYSTEM = "system"


@dataclass(frozen=True)
class Dependency:
    """A dependency edge between two packages or projects."""

    dependency_id: str
    from_project_id: str
    to_package: str
    kind: DepKind
    version_constraint: str = ""
    is_direct: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", DepKind(self.kind))
