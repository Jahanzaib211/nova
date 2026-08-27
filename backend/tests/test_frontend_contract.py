"""Backend↔frontend duplication guard for the Agent's Computer contracts.

The tab partition, status labels and event handlers all key off tool-name
strings that the BACKEND writes into sandbox.log and custom events. Two hand-
maintained lists on opposite sides of a language boundary rot independently —
this module pins them together with pure-text assertions over the real files,
the same technique the nginx header tests use.

What is pinned:
1. every tool type the harness writes as a TERMINAL-style observation
   (``bash``, ``read_file``, ``write_file``, ``str_replace``, ``shell_*``)
   must be classified Terminal by the frontend's ``tool-surface.ts``;
2. the todo-binding field name must exist on BOTH sides of the task-event
   contract (backend emitter ↔ frontend handler).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_PY = (
    REPO_ROOT
    / "backend"
    / "packages"
    / "harness"
    / "deerflow"
    / "sandbox"
    / "tools.py"
)
WORKSPACE_TOOLS_PY = (
    REPO_ROOT
    / "backend"
    / "packages"
    / "harness"
    / "deerflow"
    / "tools"
    / "builtins"
    / "workspace_tools.py"
)
TASK_TOOL_PY = (
    REPO_ROOT
    / "backend"
    / "packages"
    / "harness"
    / "deerflow"
    / "tools"
    / "builtins"
    / "task_tool.py"
)
TOOL_SURFACE_TS = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "core"
    / "threads"
    / "tool-surface.ts"
)
TASK_EVENTS_TS = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "core"
    / "threads"
    / "task-events-ws.ts"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _terminal_observation_types() -> set[str]:
    """Literal types written via _write_sandbox_observation in tools.py."""
    text = _read(TOOLS_PY)
    found = set(
        re.findall(r'_write_sandbox_observation\([^,]+,\s*"([a-z_]+)"', text)
    )
    # shell_session/shell_view live in workspace_tools.py.
    found |= set(
        re.findall(
            r'_write_sandbox_observation\(\s*_get_sandbox_id\([^)]*\),\s*"([a-z_]+)"',
            _read(WORKSPACE_TOOLS_PY),
        )
    )
    return found


class TestTerminalTypeContract:
    # Types the harness writes as terminal-style observations, split by the
    # surface the frontend must send them to. A type in NEITHER set fails the
    # test — new writers must make the classification decision here, not
    # silently in the UI.
    TERMINAL_EXPECTED = {"bash", "read_file", "write_file", "str_replace"}
    ACTIVITY_ALLOWED = {
        "browser",
        "browser_check",
        "deploy_expose",
        "notify",
        "start_dev_server",
        "stop_dev_server",
        "scaffold_project",
        "system_probe",
        "dev_verify",
        "present_files",
    }

    def test_terminal_writer_types_are_known_to_the_frontend(self) -> None:
        ts = _read(TOOL_SURFACE_TS)
        found = _terminal_observation_types()
        shell_family = {n for n in found if n.startswith("shell_")}
        unknown = found - self.TERMINAL_EXPECTED - self.ACTIVITY_ALLOWED - shell_family
        assert not unknown, (
            f"new observation types written without a contract decision: "
            f"{sorted(unknown)}. Add them to TERMINAL_EXPECTED (and to "
            "TERMINAL_TOOL_NAMES) or ACTIVITY_ALLOWED in this test."
        )
        assert shell_family, "shell_* family vanished from the writers?"
        missing = {
            name
            for name in self.TERMINAL_EXPECTED & found
            if f'"{name}"' not in ts
        }
        assert not missing, (
            "backend writes terminal observations for types the frontend "
            f"does not classify: {missing}. Add them to TERMINAL_TOOL_NAMES."
        )

    def test_shell_prefix_rule_is_present(self) -> None:
        ts = _read(TOOL_SURFACE_TS)
        assert 'TERMINAL_TOOL_PREFIXES: readonly string[] = ["shell_"]' in ts


class TestTodoBindingContract:
    def test_backend_emits_todo_indexes(self) -> None:
        py = _read(TASK_TOOL_PY)
        assert '"todo_indexes"' in py, "task_tool no longer binds todos"

    def test_frontend_consumes_todo_indexes(self) -> None:
        ts = _read(TASK_EVENTS_TS)
        assert "todo_indexes" in ts, "frontend dropped the binding field"
        assert "todoIndexes" in ts, "frontend stopped storing the binding"


class TestComputerWsChannelContract:
    """Channels the frontend routes must exist verbatim on both sides."""

    def test_terminal_stats_channel(self) -> None:
        py = _read(
            REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "sandbox" / "tools.py"
        )
        ts = _read(TASK_EVENTS_TS)
        assert 'emit_channel(\n                        "terminal_stats"' in py or \
               '"terminal_stats"' in py
        assert '"terminal-stats"' in ts or "TerminalStatsEvent" in ts

    def test_todos_channel(self) -> None:
        py = _read(TASK_TOOL_PY.parent.parent.parent / "agents" / "middlewares" / "todo_middleware.py")
        ts = _read(TASK_EVENTS_TS)
        assert 'emit_channel(\n                "todos"' in py or '"todos"' in py
        assert '"todos"' in ts

    def test_dev_server_channel(self) -> None:
        dev = _read(REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "sandbox" / "dev_server.py")
        deps = _read(REPO_ROOT / "backend" / "app" / "gateway" / "deps.py")
        assert '"channel": "browser"' in deps, "gateway must tag dev-server events channel:browser"
        # Observation events now pass through the caller's channel instead of being
        # hard-coded to "workspace", so terminal_stats / todos / etc. ride their own
        # channel. The gateway must still default to "workspace" when the payload
        # omits the channel field.
        assert 'payload.get("channel") or "workspace"' in deps, (
            "observation events must preserve their caller-supplied channel and fall "
            "back to 'workspace' when none is supplied"
        )
        assert "emit_dev_server_status" in dev, "dev_server must announce transitions"
