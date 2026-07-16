"""Project, Package, and Module models.

Phase C9 — a repository is decomposed into projects, each of which
contains packages, each of which contains modules (source files).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProjectKind(str, Enum):
    """Kind of project."""

    NODE_PROJECT = "node_project"
    PYTHON_PROJECT = "python_project"
    RUST_PROJECT = "rust_project"
    GO_PROJECT = "go_project"
    JAVA_PROJECT = "java_project"
    DOTNET_PROJECT = "dotnet_project"
    DOCKER_PROJECT = "docker_project"
    COMPOSE_PROJECT = "compose_project"
    PM2_PROJECT = "pm2_project"
    SYSTEMD_PROJECT = "systemd_project"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class PackageKind(str, Enum):
    """Kind of package within a project."""

    NPM_PACKAGE = "npm_package"
    PYTHON_PACKAGE = "python_package"
    CARGO_CRATE = "cargo_crate"
    GO_MODULE = "go_module"
    MAVEN_ARTIFACT = "maven_artifact"
    NuGet_PACKAGE = "nuget_package"
    DOCKER_IMAGE = "docker_image"
    SYSTEMD_UNIT = "systemd_unit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Project:
    """A project detected in the workspace.

    A project is a directory with a recognized project marker
    (package.json, pyproject.toml, Cargo.toml, etc.).
    """

    project_id: str
    kind: ProjectKind
    root_path: str
    name: str
    version: str = ""
    relative_to: str = ""
    packages: tuple[str, ...] = field(default_factory=tuple)
    has_tests: bool = False
    has_build: bool = False
    is_runnable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ProjectKind(self.kind))


@dataclass(frozen=True)
class Package:
    """A package within a project."""

    package_id: str
    kind: PackageKind
    project_id: str
    root_path: str
    name: str
    version: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    dev_dependencies: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PackageKind(self.kind))


@dataclass(frozen=True)
class Module:
    """A source module (file) within a package."""

    module_id: str
    project_id: str
    package_id: str
    file_path: str
    relative_path: str
    language: str
    size_bytes: int = 0
    line_count: int = 0
    symbols: tuple[str, ...] = field(default_factory=tuple)
    imports: tuple[str, ...] = field(default_factory=tuple)
    exports: tuple[str, ...] = field(default_factory=tuple)
