"""Command detector — real commands only (2026-07 audit).

Pins:
- ``migrate`` is emitted only when the project actually uses alembic
  (alembic.ini / alembic dir / dependency) — never fabricated.
- Makefile targets are detected as first-class commands.
- Node scripts keep working.
"""

import textwrap

from deerflow.workspace.detectors.command_detector import CommandDetector
from deerflow.workspace.models.command import CommandKind
from deerflow.workspace.models.project import Project, ProjectKind


def _python_project(tmp_path, **kw):
    return Project(
        project_id="p1",
        name="demo",
        kind=ProjectKind.PYTHON_PROJECT,
        root_path=str(tmp_path),
        **kw,
    )


class TestNoFabricatedMigrate:
    def test_no_migrate_without_alembic(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
        commands = CommandDetector().build_registry([_python_project(tmp_path)])
        assert "migrate" not in {c.name for c in commands}

    def test_migrate_with_alembic_ini(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
        (tmp_path / "alembic.ini").write_text("[alembic]\n")
        commands = CommandDetector().build_registry([_python_project(tmp_path)])
        migrate = [c for c in commands if c.name == "migrate"]
        assert migrate and migrate[0].kind == CommandKind.MIGRATE

    def test_migrate_with_alembic_directory(self, tmp_path):
        (tmp_path / "alembic").mkdir()
        commands = CommandDetector().build_registry([_python_project(tmp_path)])
        assert "migrate" in {c.name for c in commands}


class TestMakefileCommands:
    def test_makefile_targets_become_commands(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            textwrap.dedent(
                """
                .PHONY: test lint

                test:
                \tpytest -q

                lint:
                \truff check .

                build-all: test lint
                \techo done
                """
            )
        )
        commands = CommandDetector().build_registry([_python_project(tmp_path)])
        by_name = {c.name: c for c in commands}
        assert "test" in by_name
        assert "lint" in by_name
        assert "build-all" in by_name
        assert by_name["test"].kind == CommandKind.TEST
        assert by_name["lint"].kind == CommandKind.LINT
        assert by_name["test"].argv == ("make", "test")

    def test_special_and_pattern_targets_are_skipped(self, tmp_path):
        (tmp_path / "Makefile").write_text(".PHONY: all\n.DEFAULT_GOAL := all\n\n%.o: %.c\n\tcc -c $<\n\n$(BINDIR)/app:\n\tcc -o $@\n\nall:\n\techo ok\n")
        commands = CommandDetector().build_registry([_python_project(tmp_path)])
        names = {c.name for c in commands}
        assert "all" in names
        assert not any("%" in n or "$" in n or n.startswith(".") for n in names)

    def test_makefile_dedupes_against_project_commands(self, tmp_path):
        """A Makefile `test` target wins over the generic pytest fallback."""
        (tmp_path / "Makefile").write_text("test:\n\tpytest -x\n")
        project = _python_project(tmp_path, has_tests=True)
        commands = CommandDetector().build_registry([project])
        test_commands = [c for c in commands if c.name == "test"]
        assert len(test_commands) == 1
        assert test_commands[0].argv == ("make", "test")

    def test_no_makefile_keeps_python_fallbacks(self, tmp_path):
        project = _python_project(tmp_path, has_tests=True)
        commands = CommandDetector().build_registry([project])
        test_commands = [c for c in commands if c.name == "test"]
        assert len(test_commands) == 1
        assert test_commands[0].argv == ("pytest",)
