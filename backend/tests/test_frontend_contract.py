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
TOOLS_PY = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "sandbox" / "tools.py"
WORKSPACE_TOOLS_PY = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "tools" / "builtins" / "workspace_tools.py"
TASK_TOOL_PY = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "tools" / "builtins" / "task_tool.py"
TOOL_SURFACE_TS = REPO_ROOT / "frontend" / "src" / "core" / "threads" / "tool-surface.ts"
TASK_EVENTS_TS = REPO_ROOT / "frontend" / "src" / "core" / "threads" / "task-events-ws.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _terminal_observation_types() -> set[str]:
    """Literal types written via _write_sandbox_observation in tools.py."""
    text = _read(TOOLS_PY)
    found = set(re.findall(r'_write_sandbox_observation\([^,]+,\s*"([a-z_]+)"', text))
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
        assert not unknown, f"new observation types written without a contract decision: {sorted(unknown)}. Add them to TERMINAL_EXPECTED (and to TERMINAL_TOOL_NAMES) or ACTIVITY_ALLOWED in this test."
        assert shell_family, "shell_* family vanished from the writers?"
        missing = {name for name in self.TERMINAL_EXPECTED & found if f'"{name}"' not in ts}
        assert not missing, f"backend writes terminal observations for types the frontend does not classify: {missing}. Add them to TERMINAL_TOOL_NAMES."

    def test_shell_prefix_rule_is_present(self) -> None:
        ts = _read(TOOL_SURFACE_TS)
        assert 'TERMINAL_TOOL_PREFIXES: readonly string[] = ["shell_"]' in ts

    def test_acp_runtime_observations_have_a_frontend_rule(self) -> None:
        """`record_runtime_observation` writes `acp_<kind>` lines for an ACP
        runtime's own tool calls (the backend writer builds the type from the
        ACP kind, so the literal-type scan above cannot see it). The frontend
        must route the whole prefix to the Activity surface."""
        tools_py = _read(TOOLS_PY)
        assert 'f"acp_{safe_kind}"' in tools_py, "record_runtime_observation no longer writes acp_<kind> types?"
        ts = _read(TOOL_SURFACE_TS)
        assert 'ACP_OBSERVATION_PREFIX = "acp_"' in ts
        assert "name.startsWith(ACP_OBSERVATION_PREFIX)" in ts
        # Never a terminal type: these lines carry a title, not command output.
        assert '"acp_' not in ts.split("const TERMINAL_TOOL_NAMES")[1].split(");")[0]


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
        py = _read(REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "sandbox" / "tools.py")
        ts = _read(TASK_EVENTS_TS)
        assert 'emit_channel(\n                        "terminal_stats"' in py or '"terminal_stats"' in py
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
        assert 'payload.get("channel") or "workspace"' in deps, "observation events must preserve their caller-supplied channel and fall back to 'workspace' when none is supplied"
        assert "emit_dev_server_status" in dev, "dev_server must announce transitions"


class TestCommandCountContract:
    """The Terminal header must count the same thing on both of its branches.

    It renders the gateway's deterministic total when the computer-ws socket is
    up, and falls back to counting local events when it is not. Those two
    branches counted different populations: the backend's ``is_command_line``
    counted commands, while the fallback counted every *terminal-surface* event
    (``read_file``, ``write_file``, ``str_replace``, ``ls``, ``glob``, ``grep``
    included). Measured on a live thread that had run one command plus a read
    and a write, the same header showed "1 cmd" with the socket connected and
    "~3" without it — flipping on every reconnect.

    Neither number was wrong for what it counted; they were answers to different
    questions rendered in the same slot. So the population is pinned here, in
    both languages, rather than trusted to stay in sync.
    """

    def _backend_names(self) -> set[str]:
        text = _read(TOOLS_PY)
        match = re.search(r"COMMAND_TOOL_NAMES = frozenset\(\{([^}]*)\}\)", text)
        assert match, "backend no longer declares COMMAND_TOOL_NAMES"
        return set(re.findall(r'"([a-z_]+)"', match.group(1)))

    def _frontend_names(self) -> set[str]:
        text = _read(TOOL_SURFACE_TS)
        match = re.search(
            r"const COMMAND_TOOL_NAMES: ReadonlySet<string> = new Set\(\[(.*?)\]\)",
            text,
            re.S,
        )
        assert match, "frontend no longer declares COMMAND_TOOL_NAMES"
        return set(re.findall(r'"([a-z_]+)"', match.group(1)))

    def test_backend_declares_the_command_population(self) -> None:
        assert "bash" in self._backend_names()

    def test_the_two_sides_agree(self) -> None:
        backend = self._backend_names()
        frontend = self._frontend_names()
        # `execute_command` is a frontend-only alias for the same concept: no
        # backend writer emits it, but older/AIO event streams carry it.
        assert frontend - {"execute_command"} == backend, f"command population drifted — backend={sorted(backend)} frontend={sorted(frontend)}"

    def test_file_work_is_not_a_command(self) -> None:
        backend = self._backend_names()
        for name in ("read_file", "write_file", "str_replace", "ls", "glob", "grep"):
            assert name not in backend, f"{name} is file work, not a command"

    def test_session_bookkeeping_is_not_a_command(self) -> None:
        backend = self._backend_names()
        for name in ("shell_view", "shell_wait", "shell_kill"):
            assert name not in backend, f"{name} does not start new work"

    def test_the_header_fallback_counts_commands_not_events(self) -> None:
        """The regression itself: the fallback branch must filter."""
        terminal_tab = REPO_ROOT / "frontend" / "src" / "components" / "workspace" / "agent-computer" / "terminal-tab.tsx"
        text = _read(terminal_tab)
        assert "isCommandTool" in text, "header fallback no longer filters to commands"
        assert "`~${terminalEvents.length}`" not in text, "header fallback counts every terminal event again"


class TestDevServerSurfaceContract:
    """`dev_server` must be Terminal-visible but never counted as a command.

    It is the one type that deliberately sits in one set and not the other, so a
    careless edit that adds it to both (or drops it from both) is exactly the
    regression this pins. It arrived tagged `bash`, which put it in both, and 44%
    of the header's command total was dev-server chatter as a result.
    """

    def test_frontend_renders_it_in_the_terminal(self) -> None:
        ts = _read(TOOL_SURFACE_TS)
        match = re.search(r"const TERMINAL_TOOL_NAMES: ReadonlySet<string> = new Set\(\[(.*?)\]\)", ts, re.S)
        assert match, "TERMINAL_TOOL_NAMES is gone"
        assert '"dev_server"' in match.group(1), "dev-server output would vanish from the Terminal"

    def test_frontend_does_not_count_it(self) -> None:
        ts = _read(TOOL_SURFACE_TS)
        match = re.search(r"const COMMAND_TOOL_NAMES: ReadonlySet<string> = new Set\(\[(.*?)\]\)", ts, re.S)
        assert match, "COMMAND_TOOL_NAMES is gone"
        assert '"dev_server"' not in match.group(1), "dev-server output counted as a command again"

    def test_backend_does_not_count_it(self) -> None:
        py = _read(TOOLS_PY)
        match = re.search(r"COMMAND_TOOL_NAMES = frozenset\(\{([^}]*)\}\)", py)
        assert match, "COMMAND_TOOL_NAMES is gone"
        assert "dev_server" not in match.group(1)

    def test_the_mirror_emits_that_type(self) -> None:
        dev_server_py = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "sandbox" / "dev_server.py"
        text = _read(dev_server_py)
        assert '"dev_server"' in text, "the dev-log mirror no longer tags its lines"
        assert '"type": "bash"' not in text, "the mirror is hand-rolling a bash-tagged line again"

    def test_the_legacy_prefix_rule_survives(self) -> None:
        """Existing threads carry thousands of `[dev] ` lines tagged `bash`."""
        py = _read(TOOLS_PY)
        assert "_DEVLOG_SUMMARY_PREFIX" in py, "legacy dev lines would start counting again"
