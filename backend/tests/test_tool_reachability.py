"""Every tool the codebase advertises must be a tool the agent can actually call.

Two tools shipped fully implemented, documented, and unreachable:

* ``register_external_dev_server`` — implemented with port validation and a
  liveness probe, named in ``backend/CLAUDE.md`` as "the agent-facing tool",
  never imported into ``deerflow.tools.tools``. The Browser tab's only escape
  hatch for a dev server started outside the pipeline, dead.
* ``igino_research`` — implemented, metric-instrumented, audit-wired, and named
  *in the system prompt* ("for privacy-sensitive research, use igino_research
  instead of web_search"). Dead from 462bcf80 (2026-06-26) until 2026-08-31, so
  for two months every agent that obeyed that sentence got
  ``igino_research is not a valid tool``.

Both failed the same way: silently, and in a direction no test looked. A tool
that is defined but unbound raises nothing at import, and the model only
discovers it at call time — where the error reads like a whitelist problem
rather than a wiring one, which is exactly how the first one was misdiagnosed.

So two invariants, checked structurally rather than by reading the source:

1. every ``@tool``-decorated function is either bound or *deliberately* not;
2. every tool name the prompt or the manifest mentions resolves to a real one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow"
TOOLS_PY = HARNESS / "tools" / "tools.py"
PROMPT_PY = HARNESS / "agents" / "lead_agent" / "prompt.py"
MANIFEST_PY = HARNESS / "agents" / "manifest.py"

#: Tools defined with ``@tool`` but intentionally not in ``BUILTIN_TOOLS``,
#: each with the binding site that makes it reachable. Adding a name here is a
#: deliberate act; leaving one out is what this test exists to catch.
INTENTIONALLY_UNBOUND: dict[str, str] = {
    "task_tool": "SUBAGENT_TOOLS, bound when subagent_enabled",
    "view_image_tool": "get_available_tools(), bound when the model supports vision",
    "skill_manage_tool": "get_available_tools(), bound when skill_evolution.enabled",
    "setup_agent": "agent.py, bound when is_bootstrap",
    "update_agent": "agent.py, bound when a custom agent_name is set",
    "tool_search": "assemble_deferred_tools(), bound when tool_search.enabled",
}


def _builtin_tool_symbols() -> list[str]:
    tree = ast.parse(TOOLS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "BUILTIN_TOOLS":
                    return [e.id for e in node.value.elts if isinstance(e, ast.Name)]
    raise AssertionError("BUILTIN_TOOLS not found")


def _decorated_tools() -> dict[str, str]:
    """``{python function name: "file:line"}`` for every ``@tool`` under tools/."""
    found: dict[str, str] = {}
    for path in sorted((HARNESS / "tools").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                if (getattr(func, "id", None) or getattr(func, "attr", None)) == "tool":
                    found[node.name] = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
    return found


#: Tools the agent really has that are not declared with an ``@tool`` decorator
#: in this package: sandbox tools and community tools come from ``config.yaml``.
CONFIG_DECLARED_TOOLS = {
    "bash",
    "ls",
    "read_file",
    "write_file",
    "str_replace",
    "glob",
    "grep",
    "web_search",
    "web_fetch",
    "web_fetch_many",
    "web_crawl",
    "image_search",
    "get_ohlcv",
    "compute_indicators",
    "backtest_signals",
    "task",
    "task_status",
    "view_image",
    "invoke_acp_agent",
    "write_todos",
}


def _wire_names_by_symbol() -> dict[str, str]:
    """``{python function name: the name the model calls it by}``."""
    mapping: dict[str, str] = {}
    for path in sorted((HARNESS / "tools").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if (getattr(func, "id", None) or getattr(func, "attr", None)) != "tool":
                    continue
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    mapping[node.name] = dec.args[0].value
    return mapping


def _defined_tool_names() -> set[str]:
    """Every tool name that exists in the tree, bound or not."""
    return set(_wire_names_by_symbol().values()) | CONFIG_DECLARED_TOOLS


def _reachable_tool_names() -> set[str]:
    """Tool names the agent can actually call, unconditionally or conditionally."""
    wire = _wire_names_by_symbol()
    reachable = {wire[sym] for sym in _builtin_tool_symbols() if sym in wire}
    reachable |= {wire[sym] for sym in INTENTIONALLY_UNBOUND if sym in wire}
    return reachable | CONFIG_DECLARED_TOOLS


class TestEveryToolIsReachable:
    def test_no_tool_is_accidentally_unbound(self) -> None:
        bound = set(_builtin_tool_symbols())
        defined = _decorated_tools()
        orphans = {name: where for name, where in defined.items() if name not in bound and name not in INTENTIONALLY_UNBOUND}
        assert not orphans, "tool(s) defined but never bound and not declared intentional — this is the register_external_dev_server / igino_research defect:\n" + "\n".join(f"  {n}  ({w})" for n, w in sorted(orphans.items()))

    def test_the_intentional_list_has_no_stale_entries(self) -> None:
        """A name that got bound should be removed from the exemption list."""
        bound = set(_builtin_tool_symbols())
        stale = sorted(set(INTENTIONALLY_UNBOUND) & bound)
        assert not stale, f"now bound, so drop from INTENTIONALLY_UNBOUND: {stale}"

    def test_the_intentional_list_only_names_real_tools(self) -> None:
        defined = set(_decorated_tools())
        ghosts = sorted(set(INTENTIONALLY_UNBOUND) - defined)
        assert not ghosts, f"INTENTIONALLY_UNBOUND names non-existent tools: {ghosts}"

    def test_the_two_historically_dead_tools_are_bound(self) -> None:
        """Named explicitly so a revert is loud rather than quiet."""
        bound = set(_builtin_tool_symbols())
        for name in ("register_external_dev_server_tool", "igino_research_tool"):
            assert name in bound, f"{name} is unreachable again"


class TestNothingAdvertisesAToolThatDoesNotExist:
    """The prompt is a promise. It should not name tools the agent cannot call."""

    def _mentions(self, text: str) -> set[str]:
        """Backticked identifiers in prose.

        Deliberately compared only against names that *are* real tools: the
        prompt is full of backticked state values and config keys
        (``in_progress``, ``app_config``, ``missing_info``), and flagging those
        would make this test noise. The defect being guarded is narrower and
        sharper — a genuine tool, named to the model, that it cannot call.
        """
        # Bare identifiers too, not just backticked ones: the prompt's actual
        # igino_research references ("use web_search or igino_research to find
        # sources") carry no backticks, so a backtick-only scan missed the one
        # defect this test exists for. Widening is safe because the caller
        # intersects with real tool names, so prose can never false-positive.
        return set(re.findall(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b", text))

    def test_prompt_only_names_reachable_tools(self) -> None:
        mentioned = self._mentions(PROMPT_PY.read_text(encoding="utf-8"))
        dead = sorted((mentioned & _defined_tool_names()) - _reachable_tool_names())
        assert not dead, f"the system prompt tells the agent to use tools it cannot call — this is exactly the igino_research defect: {dead}"

    def test_manifest_only_describes_reachable_tools(self) -> None:
        text = MANIFEST_PY.read_text(encoding="utf-8")
        match = re.search(r"_TOOL_PURPOSE_OVERRIDES[^=]*=\s*\{(.*?)\n\}", text, re.S)
        assert match, "manifest no longer declares _TOOL_PURPOSE_OVERRIDES"
        described = set(re.findall(r'"([a-z_0-9]+)":', match.group(1)))
        dead = sorted(described - _reachable_tool_names())
        assert not dead, f"the spawn-time manifest describes tools the agent cannot call: {dead}"
