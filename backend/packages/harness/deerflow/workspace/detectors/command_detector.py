"""Command Detector — build the command registry from project metadata.

Phase C9 — Nova never guesses commands.  Every executable command
is inferred from project configuration and indexed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from deerflow.workspace.models.command import Command, CommandKind
from deerflow.workspace.models.project import Project, ProjectKind

SCRIPT_TO_KIND: dict[str, CommandKind] = {
    "dev": CommandKind.DEV_SERVER,
    "develop": CommandKind.DEV_SERVER,
    "start": CommandKind.SERVE,
    "serve": CommandKind.SERVE,
    "preview": CommandKind.PREVIEW,
    "build": CommandKind.BUILD,
    "compile": CommandKind.BUILD,
    "test": CommandKind.TEST,
    "tests": CommandKind.TEST,
    "lint": CommandKind.LINT,
    "eslint": CommandKind.LINT,
    "ruff": CommandKind.LINT,
    "typecheck": CommandKind.TYPE_CHECK,
    "tsc": CommandKind.TYPE_CHECK,
    "mypy": CommandKind.TYPE_CHECK,
    "format": CommandKind.FORMAT,
    "prettier": CommandKind.FORMAT,
    "black": CommandKind.FORMAT,
    "migrate": CommandKind.MIGRATE,
    "migration": CommandKind.MIGRATE,
    "db:migrate": CommandKind.MIGRATE,
    "seed": CommandKind.SEED,
    "db:seed": CommandKind.SEED,
    "worker": CommandKind.WORKER,
    "worker:start": CommandKind.WORKER,
}


class CommandDetector:
    """Build the command registry for a workspace.

    Extracts executable commands from project configuration:
    - package.json scripts
    - pyproject.toml entry-points
    - Cargo.toml binaries
    - docker-compose services
    """

    def build_registry(self, projects: list[Project]) -> list[Command]:
        """Build the complete command registry from a list of projects."""
        commands: list[Command] = []
        for project in projects:
            commands.extend(self._commands_from_project(project))
        return commands

    def _commands_from_project(self, project: Project) -> list[Command]:
        if project.kind == ProjectKind.NODE_PROJECT:
            return self._commands_from_node_project(project)
        if project.kind == ProjectKind.PYTHON_PROJECT:
            return self._commands_from_python_project(project)
        if project.kind == ProjectKind.DOCKER_PROJECT or project.kind == ProjectKind.COMPOSE_PROJECT:
            return self._commands_from_docker_project(project)
        return []

    def _commands_from_node_project(self, project: Project) -> list[Command]:
        scripts: dict = project.metadata.get("scripts", {})
        commands = []
        for name, cmd in scripts.items():
            kind = SCRIPT_TO_KIND.get(name, CommandKind.CUSTOM)
            is_background = name in {"dev", "develop", "start", "serve", "worker"}
            commands.append(
                Command(
                    command_id=uuid.uuid4().hex,
                    name=name,
                    kind=kind,
                    project_id=project.project_id,
                    argv=("npm", "run", name) if project.root_path else (),
                    cwd=project.root_path,
                    description=f"npm script: {cmd}",
                    is_background=is_background,
                    is_destructive=False,
                    metadata={"raw_script": cmd},
                )
            )
        return commands

    def _commands_from_python_project(self, project: Project) -> list[Command]:
        commands = []
        if project.has_build:
            commands.append(
                Command(
                    command_id=uuid.uuid4().hex,
                    name="build",
                    kind=CommandKind.BUILD,
                    project_id=project.project_id,
                    argv=("python", "-m", "build") if project.root_path else (),
                    cwd=project.root_path,
                    description="Build Python package",
                    is_destructive=False,
                )
            )
        if project.has_tests:
            commands.append(
                Command(
                    command_id=uuid.uuid4().hex,
                    name="test",
                    kind=CommandKind.TEST,
                    project_id=project.project_id,
                    argv=("pytest",) if project.root_path else (),
                    cwd=project.root_path,
                    description="Run Python tests",
                    is_destructive=False,
                )
            )
        commands.append(
            Command(
                command_id=uuid.uuid4().hex,
                name="migrate",
                kind=CommandKind.MIGRATE,
                project_id=project.project_id,
                argv=("alembic", "upgrade", "head") if project.root_path else (),
                cwd=project.root_path,
                description="Run database migrations",
                is_destructive=False,
            )
        )
        return commands

    def _commands_from_docker_project(self, project: Project) -> list[Command]:
        commands = []
        dc_file = None
        root = Path(project.root_path)
        for fname in ["docker-compose.yml", "docker-compose.yaml"]:
            p = root / fname
            if p.exists():
                dc_file = p
                break
        if dc_file:
            commands.append(
                Command(
                    command_id=uuid.uuid4().hex,
                    name="compose-up",
                    kind=CommandKind.COMPOSE_UP,
                    project_id=project.project_id,
                    argv=("docker", "compose", "up", "-d"),
                    cwd=str(root),
                    description="Start docker compose stack",
                    is_background=True,
                    is_destructive=False,
                )
            )
            commands.append(
                Command(
                    command_id=uuid.uuid4().hex,
                    name="compose-down",
                    kind=CommandKind.COMPOSE_DOWN,
                    project_id=project.project_id,
                    argv=("docker", "compose", "down"),
                    cwd=str(root),
                    description="Stop docker compose stack",
                    is_destructive=True,
                )
            )
        return commands
