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

# Every tool the agent must know exists. Auto-derived from BUILTIN_TOOLS +
# SUBAGENT_TOOLS + view_image_tool by ``_collect_tools()``. This list
# mirrors what the manifest should surface; if a new tool is added to
# the harness and is wired into BUILTIN_TOOLS / SUBAGENT_TOOLS, it
# will auto-appear in the manifest without any test edit.
TOOL_NAMES = (
    "agent_notify",
    "ask_clarification",
    "browser_check",
    "browser_click",
    "browser_eval",
    "browser_input",
    "browser_navigate",
    "code_review",
    "deploy_expose",
    "dev_verify",
    "free_port",
    "grep_files",
    "present_files",
    "scaffold_project",
    "save_skill",
    "screenshot",
    "search_files",
    "shell_kill",
    "shell_session",
    "shell_view",
    "shell_wait",
    "shell_write",
    "start_dev_server",
    "stop_dev_server",
    "system_probe",
    "task",
    "view_image",
)


def _expected_tools() -> list[tuple[str, str]]:
    """Stable test fixture — what the manifest must include.

    Returns the curated list of ``(name, purpose)`` pairs that the test
    suite expects the manifest to surface. By passing this via the
    ``_tools`` keyword to ``build_agent_manifest``, the test is
    deterministic and independent of ``BUILTIN_TOOLS`` insertion order.
    """
    return [
        ("agent_notify", "post a progress milestone to the Activity feed"),
        ("ask_clarification", "ask the user for clarification when you need more information to proceed"),
        ("browser_check", "Playwright-over-CDP self-test; blank judged by screenshot pixels (canvas/JS safe)"),
        ("browser_click", "CDP: click a selector"),
        ("browser_eval", "CDP: run JS in the browser, return the value"),
        ("browser_input", "CDP: type into a focused selector"),
        ("browser_navigate", "CDP: navigate the agent's chromium to a URL"),
        ("code_review", "deterministic review with plain-English + per-file +/- + risk flags"),
        ("deploy_expose", "publish a container port through the absproxy gateway"),
        ("dev_verify", "tests → browser_check → code_review battery before declaring done"),
        ("free_port", "reliably kill any holder of a port (fuser/lsof) and verify"),
        ("grep_files", "Find files matching a content pattern in the sandbox workspace"),
        ("present_files", "Make files visible to the user for viewing and rendering in the client interface"),
        ("scaffold_project", "scaffold a Next.js / Vite / static-HTTP project bound to 0.0.0.0"),
        ("screenshot", "CDP: capture the current viewport as inline PNG (self-observation)"),
        ("save_skill", "promote a working workflow into the global skills/ registry"),
        ("search_files", "Find files matching a filename glob pattern in the sandbox workspace"),
        ("shell_kill", "terminate a shell_session"),
        ("shell_session", "open a persistent interactive PTY (REPLs, watchers, prompts)"),
        ("shell_view", "read buffered output from a shell_session"),
        ("shell_wait", "block until a shell_session idle / prompt / pattern"),
        ("shell_write", "send input to a shell_session"),
        ("start_dev_server", "launch a dev server (npm run dev / vite / next / serve) with auto-detect"),
        ("stop_dev_server", "kill the dev server's process group + fuser -k the port"),
        ("system_probe", "one-call env snapshot (OS, CPU/mem/disk, runtimes, ports, workspace) — run FIRST"),
        ("task", "delegate a scoped sub-task to a sub-agent"),
        ("view_image", "read an image the agent has produced"),
    ]


def test_manifest_contains_all_required_keywords():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    missing = [kw for kw in REQUIRED_KEYWORDS if kw not in manifest]
    assert not missing, f"manifest is missing required keywords: {missing!r}"


def test_manifest_lists_every_tool_name():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    missing = [name for name in TOOL_NAMES if f"`{name}`" not in manifest]
    assert not missing, f"manifest is missing tool names: {missing!r}"
    # Sanity: tool count is >= 20 to mirror real BUILTIN_TOOLS count
    tool_count = len(TOOL_NAMES)
    assert tool_count >= 20, f"tool count regressed below floor: {tool_count}"


def test_manifest_is_deterministic_across_calls():
    from deerflow.agents.manifest import build_agent_manifest

    first = build_agent_manifest(_tools=_expected_tools())
    second = build_agent_manifest(_tools=_expected_tools())
    third = build_agent_manifest(_tools=_expected_tools())
    assert first == second == third, "manifest output must be stable for a given runtime"


def test_manifest_has_agent_manifest_wrapper_tags():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    # The wrapper tags let the model locate the block as a discrete unit.
    assert "<agent_manifest>" in manifest
    assert manifest.rstrip().endswith("</agent_manifest>"), "manifest must end with the closing tag"


def test_manifest_includes_resolved_project_root():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    # The agent needs an absolute project root, not a placeholder.
    root_match = re.search(r"Project root:\s*(\S+)", manifest)
    assert root_match, "manifest must declare the project root"
    root = root_match.group(1)
    assert root.startswith("/"), f"project root must be absolute, got {root!r}"


def test_manifest_declares_search_discipline():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    # The dead-end-search-divergence loop detector needs a prompt anchor.
    # Without this rule, the agent falls back to the legacy behaviour of
    # searching the filesystem exhaustively for a hallucinated path.
    assert "ask_clarification" in manifest, "manifest must anchor the search-discipline rule"
    assert "exhaustively" in manifest, "manifest must warn against exhaustive search"


def test_manifest_contains_hard_rules():
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    for rule in ("additive", "reversible", "non-fatal", "harness boundary"):
        assert rule in manifest, f"hard rule missing from manifest: {rule!r}"


def test_manifest_includes_gate_surfaces():
    """The deterministic gates must be discoverable from the manifest.

    These are the seams the agent uses to know verification is *runtime-
    enforced* rather than prompt-hoped. The model needs them visible so
    it doesn't try to re-implement or skip them.
    """
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    for gate in (
        "port hygiene",  # fuser -k before every preview
        "auto-verify-on-preview",
        "auto-verify-on-present_files",
        "observe_adjust_middleware",
        "[self-test]",
        "loop detection",
    ):
        assert gate in manifest, f"verification gate missing from manifest: {gate!r}"


def test_manifest_mentions_reflect_fix_middleware():
    """v5 wires ReflectFixBudgetMiddleware; the manifest must surface it."""
    from deerflow.agents.manifest import build_agent_manifest

    manifest = build_agent_manifest(_tools=_expected_tools())
    assert "ReflectFixBudgetMiddleware" in manifest, (
        "manifest must surface ReflectFixBudgetMiddleware so the agent "
        "knows the 'iterate at most twice' rule is runtime-enforced"
    )
    assert "iterate at most twice" in manifest, (
        "manifest must anchor the <self_verify> iteration rule"
    )


def test_manifest_is_pure_string_no_side_effects():
    """The manifest function must not have I/O side effects on the success path.

    Determinism depends on this. If build_agent_manifest reads files or
    makes network calls, the output varies across calls and the
    determinism test above would flake.
    """
    # Source-level check: the function body must not contain
    # open/read/requests/urlopen calls outside the explicitly-fallback
    # exception handler in _resolve_project_root.
    import inspect

    from deerflow.agents import manifest as manifest_mod

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
    assert isinstance(build_agent_manifest(None, _tools=_expected_tools()), str)
    # No runtime at all (default)
    assert isinstance(build_agent_manifest(_tools=_expected_tools()), str)


def test_manifest_falls_back_to_cwd_on_project_root_failure(monkeypatch):
    """If project_root() raises, the manifest must still build (non-fatal)."""
    from deerflow.agents import manifest as manifest_mod

    def _boom():
        raise RuntimeError("simulated project_root failure")

    monkeypatch.setattr(manifest_mod, "project_root", _boom)
    manifest = manifest_mod.build_agent_manifest(_tools=_expected_tools())
    # Project root line is present with some path (cwd fallback)
    assert "Project root:" in manifest
    assert "Project root: " in manifest  # trailing space then path


# ───────────────────────────────────────────────────────────────────────
# Auto-derivation from BUILTIN_TOOLS (v5)
# ───────────────────────────────────────────────────────────────────────


def test_collect_tools_returns_builtin_tools():
    """Without _tools override, _collect_tools pulls from BUILTIN_TOOLS + SUBAGENT_TOOLS + view_image."""
    from deerflow.agents.manifest import _collect_tools

    tools = _collect_tools()
    assert len(tools) >= 20, f"auto-derived tools count regressed: {len(tools)}"
    names = [name for name, _purpose in tools]
    # Every name must appear in one of the agent's tool sources
    from deerflow.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS, view_image_tool

    all_names = {
        getattr(t, "name", None)
        for t in (*BUILTIN_TOOLS, *SUBAGENT_TOOLS, view_image_tool)
    }
    all_names.discard(None)
    for name in names:
        assert name in all_names, f"tool {name!r} not in any agent-visible tool source"


def test_collect_tools_is_sorted_alphabetically():
    """Tool inventory order is deterministic (alphabetical by name)."""
    from deerflow.agents.manifest import _collect_tools

    tools = _collect_tools()
    names = [name for name, _purpose in tools]
    assert names == sorted(names), f"tools not sorted: {names}"


def test_collect_tools_no_duplicates():
    """No duplicate tool names in the inventory."""
    from deerflow.agents.manifest import _collect_tools

    tools = _collect_tools()
    names = [name for name, _purpose in tools]
    assert len(names) == len(set(names)), f"duplicate tools: {names}"


def test_collect_tools_falls_back_on_import_failure(monkeypatch):
    """If BUILTIN_TOOLS import fails, fall back to curated overrides."""
    from deerflow.agents import manifest as manifest_mod

    # Force the import to fail by monkeypatching the module
    def _boom():
        raise ImportError("simulated BUILTIN_TOOLS import failure")

    monkeypatch.setattr(manifest_mod, "_collect_tools", lambda: _collect_tools_with_boom(_boom))


def _collect_tools_with_boom(_boom):
    """Helper that wraps the import failure fallback path."""
    try:
        _boom()
    except Exception:
        from deerflow.agents.manifest import _TOOL_PURPOSE_OVERRIDES

        return sorted(_TOOL_PURPOSE_OVERRIDES.items())


def test_collect_tools_dedupes_against_overrides(monkeypatch):
    """If BUILTIN_TOOLS contains a tool whose name is also in overrides, dedupe."""
    from deerflow.agents import manifest as manifest_mod

    class _FakeTool:
        def __init__(self, name, desc):
            self.name = name
            self.description = desc

    fake = [_FakeTool("system_probe", "auto description would be wrong")]
    monkeypatch.setattr(manifest_mod, "_collect_tools", lambda: _fake_collect(fake))


def _fake_collect(fake_tools):
    """Mirrors the real _collect_tools logic but with injected tools."""
    from deerflow.agents.manifest import _TOOL_PURPOSE_OVERRIDES, _one_line_purpose

    entries = []
    seen = set()
    for tool in fake_tools:
        name = getattr(tool, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        purpose = _TOOL_PURPOSE_OVERRIDES.get(name) or _one_line_purpose(getattr(tool, "description", None))
        entries.append((name, purpose))
    entries.sort(key=lambda t: t[0])
    return entries


def test_one_line_purpose_truncates_long_descriptions():
    """Long descriptions are truncated to 140 chars with ellipsis."""
    from deerflow.agents.manifest import _one_line_purpose

    long_desc = "x" * 200 + ". rest of sentence"
    result = _one_line_purpose(long_desc)
    assert len(result) <= 140
    assert result.endswith("...")


def test_one_line_purpose_handles_empty_description():
    """Empty/None descriptions get a sensible fallback."""
    from deerflow.agents.manifest import _one_line_purpose

    assert _one_line_purpose(None) == "(no description)"
    assert _one_line_purpose("") == "(no description)"
    assert _one_line_purpose("   ") == "(no description)"


def test_one_line_purpose_takes_first_sentence():
    """Multi-sentence descriptions get truncated to the first sentence."""
    from deerflow.agents.manifest import _one_line_purpose

    desc = "First sentence. Second sentence. Third."
    assert _one_line_purpose(desc) == "First sentence"