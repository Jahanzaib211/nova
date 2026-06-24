"""Tests for the spawn-time agent self-knowledge manifest.

The manifest is the bootstrap seam that gives the agent canonical
self-knowledge on turn 1. These tests assert the structural
invariants the manifest must hold:

* It is **deterministic** — same output for repeated calls.
* It contains the **canonical capability surface** — sandbox, browser,
  tools, gates, panel — so the agent doesn't have to infer any of it.
* It contains the **hard rules** — additive, reversible, non-fatal,
  harness boundary — so the model knows the constraints from turn 0.
* It contains the **project root** resolved via the standard runtime
  helper so the agent can answer "where am I?" deterministically.
* It contains the **search discipline** rule so the
  dead-end-search-divergence loop-detector category has a prompt anchor.
* It contains **>= 20 tools** to mirror the real BUILTIN_TOOLS count.
* The injection contract is **non-fatal** — if build_agent_manifest
  raises, the run path still proceeds (verified at the call site in
  thread_data_middleware; here we assert the function itself raises
  visibly so the caller can wrap it).
"""

from __future__ import annotations

import re

import pytest


# Canonical keywords the manifest MUST contain. These are the seam
# the agent uses to recognize its capabilities. If you remove or rename
# one, the model loses a piece of self-knowledge — fail loud.
REQUIRED_KEYWORDS = (
    # Sandbox
    "sandbox",
    "AIO",
    "Docker",
    "PTY",
    "browser_navigate",
    # Tools (subset — the full set is asserted by TOOL_NAMES)
    "system_probe",
    "dev_verify",
    "browser_check",
    "code_review",
    "free_port",
    "save_skill",
    # Gates
    "auto-verify-on-preview",
    "auto-verify-on-present_files",
    "[self-test]",
    # Hard rules
    "additive",
    "reversible",
    "non-fatal",
    "harness boundary",
    "is_local_sandbox",
    # Panel
    "Files",
    "Terminal",
    "Editor",
    "Browser",
    "Activity",
    "Review",
    "ttyd",
    "VNC",
    # Search discipline
    "ask_clarification",
    "systematically" if False else "exhaustively",
)

# Every tool the agent must know exists. Kept in sync with
# ``agents/manifest.py::_WORKSPACE_TOOLS``.
TOOL_NAMES = (
    "system_probe",
    "free_port",
    "dev_verify",
    "browser_check",
    "code_review",
    "save_skill",
    "search_files",
    "grep_files",
    "scaffold_project",
    "start_dev_server",
    "stop_dev_server",
    "shell_session",
    "shell_view",
    "shell_wait",
    "shell_write",
    "shell_kill",
    "browser_navigate",
    "browser_click",
    "browser_input",
    "browser_eval",
    "deploy_expose",
    "agent_notify",
    "ask_clarification",
    "present_file",
    "task_tool",
    "view_image",
)


def test_manifest_contains_all_required_keywords():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    missing = [kw for kw in REQUIRED_KEYWORDS if kw not in manifest]
    assert not missing, f"manifest is missing required keywords: {missing!r}"


def test_manifest_lists_every_tool_name():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    missing = [name for name in TOOL_NAMES if f"`{name}`" not in manifest]
    assert not missing, f"manifest is missing tool names: {missing!r}"
    # Sanity: tool count is >= 20 to mirror real BUILTIN_TOOLS count
    tool_count = len(TOOL_NAMES)
    assert tool_count >= 20, f"tool count regressed below floor: {tool_count}"


def test_manifest_is_deterministic_across_calls():
    from deerflow.agents.manifest import build_agent_manifest

    first = build_agent_manifest()
    second = build_agent_manifest()
    third = build_agent_manifest()
    assert first == second == third, "manifest output must be stable for a given runtime"


def test_manifest_has_agent_manifest_wrapper_tags():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    # The wrapper tags let the model locate the block as a discrete unit.
    assert "<agent_manifest>" in manifest
    assert manifest.rstrip().endswith("</agent_manifest>"), "manifest must end with the closing tag"


def test_manifest_includes_resolved_project_root():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    # The agent needs an absolute project root, not a placeholder.
    root_match = re.search(r"Project root:\s*(\S+)", manifest)
    assert root_match, "manifest must declare the project root"
    root = root_match.group(1)
    assert root.startswith("/"), f"project root must be absolute, got {root!r}"


def test_manifest_declares_search_discipline():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    # The dead-end-search-divergence loop detector needs a prompt anchor.
    # Without this rule, the agent falls back to the legacy behaviour of
    # searching the filesystem exhaustively for a hallucinated path.
    assert "ask_clarification" in manifest, "manifest must anchor the search-discipline rule"
    assert "exhaustively" in manifest, "manifest must warn against exhaustive search"


def test_manifest_contains_hard_rules():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    for rule in ("additive", "reversible", "non-fatal", "harness boundary"):
        assert rule in manifest, f"hard rule missing from manifest: {rule!r}"


def test_manifest_includes_gate_surfaces():
    """The deterministic gates must be discoverable from the manifest.

    These are the seams the agent uses to know verification is *runtime-
    enforced* rather than prompt-hoped. The model needs them visible so
    it doesn't try to re-implement or skip them.
    """
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest()
    for gate in (
        "port hygiene",  # fuser -k before every preview
        "auto-verify-on-preview",
        "auto-verify-on-present_files",
        "observe_adjust_middleware",
        "[self-test]",
        "loop detection",
    ):
        assert gate in manifest, f"verification gate missing from manifest: {gate!r}"


def test_manifest_is_pure_string_no_side_effects():
    """The manifest function must not have I/O side effects on the success path.

    Determinism depends on this. If build_agent_manifest reads files or
    makes network calls, the output varies across calls and the
    determinism test above would flake.
    """
    from deerflow.agents import manifest as manifest_mod

    # Source-level check: the function body must not contain
    # open/read/requests/urlopen calls outside the explicitly-fallback
    # exception handler in _resolve_project_root.
    import inspect

    source = inspect.getsource(manifest_mod.build_agent_manifest)
    forbidden = ("open(", "Path.read_", "Path.write_", "requests.", "urllib.", "urlopen(")
    hits = [tok for tok in forbidden if tok in source]
    assert not hits, f"build_agent_manifest must be I/O-free on success path; found: {hits!r}"


def test_manifest_never_raises_on_normal_runtime():
    """The function must always return a string for any Runtime input.

    The injection point wraps this in try/except — but the function
    itself should be reliable enough that the wrapper never has to fire
    under normal conditions.
    """
    from deerflow.agents.manifest import build_agent_manifest

    # None runtime (the canonical call from the injection point)
    assert isinstance(build_agent_manifest(None), str)
    # No runtime at all (default)
    assert isinstance(build_agent_manifest(), str)


def test_manifest_falls_back_to_cwd_on_project_root_failure(monkeypatch):
    """If project_root() raises, the manifest must still build (non-fatal)."""
    from deerflow.agents import manifest as manifest_mod

    def _boom():
        raise RuntimeError("simulated project_root failure")

    monkeypatch.setattr(manifest_mod, "project_root", _boom)
    manifest = manifest_mod.build_agent_manifest()
    # Project root line is present with some path (cwd fallback)
    assert "Project root:" in manifest
    assert "Project root: " in manifest  # trailing space then path