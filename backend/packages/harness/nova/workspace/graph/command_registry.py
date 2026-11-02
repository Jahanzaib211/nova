"""Command Registry — indexed repository of known project commands.

Phase C9 — provides fast lookup of project commands by name, kind,
or project.  Replaces the pattern of scanning package.json or
Makefile at need.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from deerflow.workspace.models.command import Command, CommandKind


@dataclass
class CommandRegistry:
    """In-memory registry of all known project commands.

    Built once by CommandDetector.  Queryable by name (exact/prefix),
    kind, and project_id.  Thread-safe for concurrent reads.
    """

    _by_name: dict[str, list[Command]] = field(default_factory=lambda: defaultdict(list))
    _by_kind: dict[CommandKind, list[Command]] = field(default_factory=lambda: defaultdict(list))
    _by_project: dict[str, list[Command]] = field(default_factory=lambda: defaultdict(list))
    _all: list[Command] = field(default_factory=list)

    def register(self, command: Command) -> None:
        """Add a command to the registry."""
        self._all.append(command)
        self._by_name[command.name].append(command)
        self._by_kind[command.kind].append(command)
        if command.project_id:
            self._by_project[command.project_id].append(command)

    def register_many(self, commands: list[Command]) -> None:
        """Add multiple commands."""
        for cmd in commands:
            self.register(cmd)

    def find_by_name(self, name: str) -> list[Command]:
        """Find all commands with this exact name."""
        return list(self._by_name.get(name, []))

    def find_by_prefix(self, prefix: str) -> list[Command]:
        """Find all commands whose name starts with ``prefix``."""
        results = []
        for name, cmds in self._by_name.items():
            if name.startswith(prefix):
                results.extend(cmds)
        return results

    def find_by_kind(self, kind: CommandKind) -> list[Command]:
        """Find all commands of this kind."""
        return list(self._by_kind.get(kind, []))

    def find_by_project(self, project_id: str) -> list[Command]:
        """Find all commands belonging to a project."""
        return list(self._by_project.get(project_id, []))

    def find_test_commands(self) -> list[Command]:
        """Shorthand for finding all test commands."""
        return self.find_by_kind(CommandKind.TEST)

    def find_dev_commands(self) -> list[Command]:
        """Shorthand for finding all dev commands."""
        return self.find_by_kind(CommandKind.DEV)

    def find_build_commands(self) -> list[Command]:
        """Shorthand for finding all build commands."""
        return self.find_by_kind(CommandKind.BUILD)

    def count(self) -> int:
        return len(self._all)

    def all_commands(self) -> list[Command]:
        return list(self._all)
