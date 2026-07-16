"""Project Detector — find all projects in a workspace.

Phase C9 — walks the workspace to find all project markers
(package.json, pyproject.toml, Cargo.toml, etc.).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from deerflow.workspace.models.project import Project, ProjectKind
from deerflow.workspace.scanners.walker import BoundedWalker, TraversalLimit

PROJECT_MARKERS: dict[str, ProjectKind] = {
    "package.json": ProjectKind.NODE_PROJECT,
    "pyproject.toml": ProjectKind.PYTHON_PROJECT,
    "setup.py": ProjectKind.PYTHON_PROJECT,
    "requirements.txt": ProjectKind.PYTHON_PROJECT,
    "Pipfile": ProjectKind.PYTHON_PROJECT,
    "Cargo.toml": ProjectKind.RUST_PROJECT,
    "go.mod": ProjectKind.GO_PROJECT,
    "pom.xml": ProjectKind.JAVA_PROJECT,
    "build.gradle": ProjectKind.JAVA_PROJECT,
    "Dockerfile": ProjectKind.DOCKER_PROJECT,
    "docker-compose.yml": ProjectKind.COMPOSE_PROJECT,
    "docker-compose.yaml": ProjectKind.COMPOSE_PROJECT,
    "pm2.config.js": ProjectKind.PM2_PROJECT,
    "ecosystem.config.js": ProjectKind.PM2_PROJECT,
}


class ProjectDetector:
    """Find all projects in a workspace.

    Walks the workspace tree looking for project markers.
    Each project is assigned a stable ``project_id``.
    """

    def find_all(self, root: Path) -> list[Project]:
        """Find all projects under ``root``."""
        walker = BoundedWalker(root, TraversalLimit(max_depth=4, max_files=20_000))
        projects: list[Project] = []
        seen_roots: set[str] = set()

        for entry in walker.walk():
            root_path = Path(entry.root)
            for marker, kind in PROJECT_MARKERS.items():
                if marker in entry.files:
                    marker_path = root_path / marker
                    if str(marker_path) in seen_roots:
                        continue
                    seen_roots.add(str(marker_path))
                    project = self._build_project(marker_path, kind, root)
                    if project:
                        projects.append(project)

        return projects

    def _build_project(self, marker_path: Path, kind: ProjectKind, workspace_root: Path) -> Project:
        root = marker_path.parent
        name = self._project_name(marker_path, kind)
        metadata: dict = {}

        if kind == ProjectKind.NODE_PROJECT:
            pkg = self._read_json(marker_path)
            if pkg:
                metadata = {"scripts": pkg.get("scripts", {}), "dependencies": pkg.get("dependencies", {}), "devDependencies": pkg.get("devDependencies", {})}
                name = pkg.get("name", name)

        elif kind == ProjectKind.PYTHON_PROJECT:
            if marker_path.suffix == ".toml":
                meta = self._read_toml(marker_path)
                if meta:
                    name = meta.get("project", {}).get("name", name) or meta.get("name", name)
            elif marker_path.name == "requirements.txt":
                deps = self._read_requirements(marker_path)
                metadata = {"dependencies": deps}

        elif kind == ProjectKind.RUST_PROJECT:
            cargo = self._read_toml(marker_path)
            if cargo:
                name = cargo.get("package", {}).get("name", name)
                metadata = {"dependencies": list(cargo.get("dependencies", {}).keys())}

        relative_to = str(root.relative_to(workspace_root)) if root != workspace_root else ""

        return Project(
            project_id=uuid.uuid4().hex,
            kind=kind,
            root_path=str(root),
            name=name,
            version="",
            relative_to=relative_to,
            has_tests=self._has_tests(root, kind),
            has_build=self._has_build(root, kind, metadata),
            is_runnable=self._is_runnable(metadata, kind),
            metadata=metadata,
        )

    def _project_name(self, marker_path: Path, kind: ProjectKind) -> str:
        return marker_path.parent.name

    def _read_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_toml(self, path: Path) -> dict | None:
        try:
            import tomllib

            return tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_requirements(self, path: Path) -> list[str]:
        try:
            return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
        except Exception:
            return []

    def _has_tests(self, root: Path, kind: ProjectKind) -> bool:
        if kind == ProjectKind.NODE_PROJECT:
            return (root / "test").exists() or (root / "tests").exists() or (root / "__tests__").exists()
        if kind == ProjectKind.PYTHON_PROJECT:
            return (root / "tests").exists() or (root / "test").exists()
        if kind == ProjectKind.RUST_PROJECT:
            return (root / "tests").exists() or (root / "src").exists()
        return False

    def _has_build(self, root: Path, kind: ProjectKind, metadata: dict) -> bool:
        if kind == ProjectKind.NODE_PROJECT:
            scripts = metadata.get("scripts", {})
            return bool(scripts.get("build") or scripts.get("prebuild"))
        if kind == ProjectKind.PYTHON_PROJECT:
            return bool(metadata.get("dependencies"))
        return False

    def _is_runnable(self, metadata: dict, kind: ProjectKind) -> bool:
        if kind == ProjectKind.NODE_PROJECT:
            return bool(metadata.get("scripts", {}).get("dev") or metadata.get("scripts", {}).get("start"))
        return False
