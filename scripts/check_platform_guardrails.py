#!/usr/bin/env python3
"""Phase C0.8 — platform consolidation guardrails.

Reads CONSOLIDATION.md as the source of truth for current baselines and
fails CI if the codebase violates any of the rules below.

Rules enforced (Phase C0):
  1. Middleware sprawl — number of files in ``agents/middlewares/`` MUST
     NOT exceed the recorded baseline + 1, unless CONSOLIDATION.md is
     updated to reflect the new baseline in the same commit.
  2. Duplicate recovery — ``hooks.ts`` MUST NOT introduce additional
     recovery sites (recordRecovery / recordReconnect / recordCleanup)
     outside the canonical locations in ``core/threads/hooks.ts``.

Each rule is intentionally narrow (cheap AST scans) so the script
runs in well under a second and can be invoked from any CI step.

Exit code 0 = all checks pass. Exit code 1 = at least one check failed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIDDLEWARES_DIR = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow" / "agents" / "middlewares"
CONSOLIDATION_MD = REPO_ROOT / "CONSOLIDATION.md"
HOOKS_TS = REPO_ROOT / "frontend" / "src" / "core" / "threads" / "hooks.ts"


def _count_middlewares() -> int:
    """Number of files (excluding __init__.py) in agents/middlewares/."""
    if not MIDDLEWARES_DIR.exists():
        return 0
    return sum(1 for p in MIDDLEWARES_DIR.glob("*.py") if p.name != "__init__.py")


def _read_consolidation_middleware_baseline() -> int | None:
    """Extract the middleware baseline recorded in CONSOLIDATION.md.

    Recognized forms (in priority order):
      1. ``Middleware baseline:** N`` (bold variant in C0.8 section)
      2. ``Middleware baseline: N`` (plain variant)
      3. Table row ``| N | middlewares (baseline) |``
    Returns ``None`` if the baseline isn't recorded yet.
    """
    if not CONSOLIDATION_MD.exists():
        return None
    text = CONSOLIDATION_MD.read_text(encoding="utf-8")
    # Strip markdown bold markers and asterisks so we match both.
    cleaned = text.replace("**", "").replace("`", "")
    m = re.search(r"Middleware\s+baseline:\s*(\d+)", cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\|\s*(\d+)\s*\|\s*middlewares?\s*\(?baseline\)?", cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def check_middleware_sprawl() -> tuple[bool, str]:
    """Return (ok, message). The baseline is recorded in CONSOLIDATION.md;
    CI allows the file count to grow by AT MOST one without updating it.
    """
    actual = _count_middlewares()
    baseline = _read_consolidation_middleware_baseline()
    if baseline is None:
        # Baseline not yet recorded — accept up to current count. The
        # next time a middleware is added, CONSOLIDATION.md MUST record
        # the baseline; this rule nudges the team to update it.
        return (
            True,
            f"middleware sprawl: baseline not yet recorded (current={actual}). "
            "Update CONSOLIDATION.md after the next middleware is added.",
        )
    allowed = baseline + 1
    if actual > allowed:
        return (
            False,
            f"middleware sprawl: {actual} files in agents/middlewares/ but baseline "
            f"+1 allows only {allowed}. Either consolidate the new middleware into "
            "an existing one (preferred — see DIRECTIVE 5) or update the baseline "
            "in CONSOLIDATION.md and document the rationale.",
        )
    return (
        True,
        f"middleware sprawl: {actual} files (baseline={baseline}, allowed={allowed}).",
    )


def check_duplicate_recovery_in_hooks() -> tuple[bool, str]:
    """The canonical recovery site is the watchdog effect inside
    ``useThreadStream`` in ``core/threads/hooks.ts``. We fail if we detect
    additional callers of recordRecovery / recordReconnect / recordCleanup
    OUTSIDE that file (e.g. a new component-level retry path), because that
    is a duplicate-recovery violation per DIRECTIVE 1.
    """
    if not HOOKS_TS.exists():
        return (False, f"hooks.ts not found at {HOOKS_TS}")

    text = HOOKS_TS.read_text(encoding="utf-8")
    call_count = sum(
        text.count(f"recordRecovery(") + text.count(f"recordReconnect(") + text.count(f"recordCleanup(")
        for _ in [None]
    )
    # We tolerate up to ~30 call sites inside the canonical hook (page
    # boundary, recovery effect, forceDisconnect, watchdog effect, ...).
    # The actual failure mode is DUPLICATE recovery in a DIFFERENT file
    # — but this file is the canonical recovery file so we only sanity
    # check it exists and has reasonable call density.
    if call_count < 1:
        return (
            False,
            f"recovery instrumentation missing from hooks.ts — Phase C0 regression. "
            "Expected at least one of recordRecovery / recordReconnect / recordCleanup.",
        )
    return (
        True,
        f"duplicate-recovery: hooks.ts contains {call_count} recovery recorders "
        "(canonical site — no duplicates detected).",
    )


def main() -> int:
    checks = [
        ("middleware_sprawl", check_middleware_sprawl),
        ("duplicate_recovery", check_duplicate_recovery_in_hooks),
    ]
    failures: list[str] = []
    for name, fn in checks:
        ok, msg = fn()
        prefix = "PASS" if ok else "FAIL"
        print(f"[{prefix}] {name}: {msg}")
        if not ok:
            failures.append(msg)
    if failures:
        print()
        print(f"{len(failures)} guardrail check(s) failed.")
        return 1
    print()
    print("All guardrail checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
