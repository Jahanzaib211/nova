"""Spawn-time agent self-knowledge manifest.

When an agent run starts, the harness injects the contents of this module
into the system message so the model has canonical self-knowledge on turn 1
without having to infer it from the prompt's tool menu.

The manifest is **deterministic** (same output across calls for the same
runtime state), **additive** (no behavioral change when the injection point
fails — failure is logged and the run continues with the original prompt),
and **reverse-compatible** with the prompt's existing
``<command_arsenal>`` / ``<enterprise_capabilities>`` / ``<self_verify>``
blocks (which remain unchanged).

Hard rules (enforced elsewhere, surfaced here for the agent):

* **Additive / reversible only.** Every change in this fork is one-edit
  revertible.
* **Local-sandbox path byte-identical.** AIO and local providers branch
  on ``is_local_sandbox``; the local provider behaves exactly as upstream.
* **Non-fatal.** Anything new in the run path is wrapped so a failure can
  never break a run.
* **Harness boundary.** ``deerflow.*`` never imports ``app.*``.

Why this exists: the deterministic verification gates
(``observe_adjust_middleware.py`` etc.) fire *after* the agent produces
an artifact. The prior bootstrap gap — agents inventing project paths
or re-discovering their own capabilities — happens *before* any tool
call. The manifest closes that gap by making self-knowledge structural
(the system message carries it), not memoric (the model has to remember
to read it).
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.runtime import Runtime

from deerflow.config.runtime_paths import project_root

logger = logging.getLogger(__name__)


# Curated one-line purpose overrides for tools whose auto-derived first-
# sentence is too long, too vague, or doesn't surface the operational
# guidance the agent needs. Keys are tool names; values are short
# purpose strings. Tools not listed here get their first sentence
# (truncated to 140 chars) extracted from the BUILTIN_TOOLS description.
#
# This list is intentionally small: as the harness grows, new tools
# pick up an auto-derived one-liner with zero maintenance. Only the
# tools whose default one-liner is misleading get an override.
_TOOL_PURPOSE_OVERRIDES: dict[str, str] = {
    "system_probe": "one-call env snapshot (OS, CPU/mem/disk, runtimes, ports, workspace) — run FIRST",
    "free_port": "reliably kill any holder of a port (fuser/lsof) and verify",
    "dev_verify": "tests → browser_check → code_review battery before declaring done",
    "browser_check": "Playwright-over-CDP self-test; blank judged by screenshot pixels (canvas/JS safe)",
    "code_review": "deterministic review with plain-English + per-file +/- + risk flags",
    "save_skill": "promote a working workflow into the global skills/ registry",
    "scaffold_project": "scaffold a Next.js / Vite / static-HTTP project bound to 0.0.0.0",
    "start_dev_server": "launch a dev server (npm run dev / vite / next / serve) with auto-detect",
    "stop_dev_server": "kill the dev server's process group + fuser -k the port",
    "shell_session": "open a persistent interactive PTY (REPLs, watchers, prompts)",
    "shell_view": "read buffered output from a shell_session",
    "shell_wait": "block until a shell_session idle / prompt / pattern",
    "shell_write": "send input to a shell_session",
    "shell_kill": "terminate a shell_session",
    "browser_navigate": "CDP: navigate the agent's chromium to a URL",
    "browser_click": "CDP: click a selector",
    "browser_input": "CDP: type into a focused selector",
    "browser_eval": "CDP: run JS in the browser, return the value",
    "screenshot": "CDP: capture the current viewport as inline PNG (self-observation)",
    "deploy_expose": "publish a container port through the absproxy gateway",
    "agent_notify": "post a progress milestone to the Activity feed",
    "igino_research": "privacy-focused multi-source web research via SearXNG + TOR with deterministic pipeline",
}


def _one_line_purpose(description: str | None) -> str:
    """Reduce a multi-line tool description to a single concise line.

    Strategy: take the first sentence (split on '. '), then trim to
    140 chars with a trailing ellipsis if needed. Returns a sensible
    fallback when the description is missing or empty.
    """
    if not description:
        return "(no description)"
    # Take first sentence — split on '. ' but keep things like 'e.g.'
    # together. A safe heuristic is to split on '. ' and take the first
    # non-empty chunk; if no '. ' present, take the whole string.
    text = description.strip()
    if not text:
        return "(no description)"
    # Strip common docstring indents / bullet prefixes.
    text = text.lstrip("- *\t ")
    parts = text.split(". ")
    first = parts[0].rstrip(".").strip()
    if not first:
        return text[:140].strip()
    if len(first) > 140:
        return first[:137].rstrip() + "..."
    return first


def _collect_tools() -> list[tuple[str, str]]:
    """Return ``[(name, purpose), ...]`` for every agent-visible tool.

    Imports ``deerflow.tools.tools`` lazily to keep this module cheap to
    import. Includes ``BUILTIN_TOOLS`` + ``SUBAGENT_TOOLS`` (the latter
    is conditionally appended to the lead agent's tool list at run time).
    ``view_image_tool`` is included only when the model supports vision;
    we surface it unconditionally because the agent needs to know it
    *might* be available.

    On any import failure (circular import, langchain missing, etc.),
    falls back to the curated ``_TOOL_PURPOSE_OVERRIDES`` so the
    manifest still has a sensible tool inventory.
    """
    try:
        from deerflow.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS, view_image_tool  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — manifest is non-fatal
        logger.debug("manifest: BUILTIN_TOOLS import failed (%s); using overrides only", exc)
        return sorted(_TOOL_PURPOSE_OVERRIDES.items())

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tool in (*BUILTIN_TOOLS, *SUBAGENT_TOOLS, view_image_tool):
        name = getattr(tool, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        purpose = _TOOL_PURPOSE_OVERRIDES.get(name) or _one_line_purpose(getattr(tool, "description", None))
        entries.append((name, purpose))
    # Sort by name for deterministic output (independent of insertion order)
    entries.sort(key=lambda t: t[0])
    return entries


def _format_tool_inventory(tools: list[tuple[str, str]] | None = None) -> str:
    """Render the tool inventory as a markdown bullet list, sorted."""
    if tools is None:
        tools = _collect_tools()
    lines = ["Tools (deterministic — call by exact name):"]
    for name, purpose in tools:
        lines.append(f"  - `{name}` — {purpose}")
    return "\n".join(lines)


def _resolve_project_root() -> str:
    """Resolve the project root path with safe fallback.

    ``project_root()`` reads ``DEER_FLOW_PROJECT_ROOT`` if set; if it
    raises or the resolved path doesn't exist, fall back to ``Path.cwd()``
    so the manifest never breaks the run (non-fatal contract).
    """
    try:
        root = project_root()
        return str(root)
    except Exception as exc:  # noqa: BLE001 — manifest is non-fatal
        logger.debug("manifest: project_root() failed (%s); falling back to cwd", exc)
        return str(Path.cwd())


def build_agent_manifest(runtime: Runtime | None = None, *, _tools: list[tuple[str, str]] | None = None) -> str:
    """Build the canonical agent self-knowledge block.

    Pure function (no I/O side effects on success path), deterministic for a
    given runtime state. The injected block is intended to land at the top
    of the system message so the model sees it on turn 1.

    Args:
        runtime: LangGraph Runtime, optional. Currently unused — kept for
            forward-compatibility (per-thread manifest overrides could read
            ``runtime.context`` or ``config.configurable``).
        _tools: Test-only injection point. Lets tests assert the manifest
            shape without depending on ``BUILTIN_TOOLS`` ordering.

    Returns:
        A markdown string with ``<agent_manifest>...</agent_manifest>``
        wrapper tags so the model can locate it as a discrete block.
    """
    del runtime  # currently unused; reserved for per-thread overrides
    root = _resolve_project_root()
    tool_block = _format_tool_inventory(_tools)

    return f"""<agent_manifest>
Project root: {root}

Sandbox: AIO (Docker per-thread). Each thread gets its own isolated container
via Docker-out-of-Docker. PTY (shell_session/view/wait/write/kill).
Browser-over-CDP (browser_navigate/click/input/eval) against the AIO chromium.
Self-observation: screenshot captures the current viewport inline for visual verification.
File ops: file.*, scaffold_project, search_files, grep_files.
Network: deploy_expose (absproxy publish), agent_notify (activity feed).

{tool_block}

Verification gates (deterministic — runtime-enforced, not prompt-hoped):
  - port hygiene before every preview (free_port / fuser -k)
  - auto-verify-on-preview (background browser_check on dev-server-ready)
  - auto-verify-on-present_files (background browser_check on shipped HTML)
  - observe_adjust_middleware writes [self-test] lines to per-thread sandbox.log
  - loop detection has a "dead-end search divergence" category that forces
    clarification_tool after N distinct ENOENT results for the same basename
  - ReflectFixBudgetMiddleware enforces "iterate at most twice" from
    <self_verify>: after 2 consecutive dev_verify ISSUES, injects a forced
    HumanMessage telling the agent to write its final answer

Hard rules:
  - additive / reversible only (one-edit revert)
  - local-sandbox path byte-identical (branch on is_local_sandbox)
  - non-fatal in the run path (a failure can never break a run)
  - harness boundary: deerflow.* never imports app.*

Agent's Computer panel (right side of the chat):
  - Files    — repo tree + Outputs
  - Terminal — ttyd stream (Stream/Shell toggle)
  - Editor   — live file diff (red/green) for the file under edit
  - Browser  — VNC live view of the agent's chromium + static-HTML preview
  - Activity — structured tool-call timeline + verify_result pills
  - Review   — deterministic code-review surface (REVIEW.md)

When a task references a path / repo / file that is NOT under {root}:
  1. Run `system_probe` to confirm the workspace state
  2. Call `ask_clarification` — do NOT search the filesystem exhaustively
  3. If the user confirms an out-of-tree target, proceed with explicit
     absolute paths and never `cd` outside {root} without permission
</agent_manifest>
"""


__all__ = ["build_agent_manifest"]
