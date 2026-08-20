#!/usr/bin/env python3
"""Assert that docs/CI_CD.md still describes reality.

Why this exists
---------------
`docs/CI_CD.md` carries a "Test Counts (canonical)" table and a workflow count.
Both had silently drifted from the code they describe — 6,430 vs 6,489 backend
tests, 565 vs 568 frontend, 73 vs 80 Playwright, "15 workflows" vs 16. Nothing
checked, so the numbers rotted quietly and were only caught by counting them by
hand during an audit.

`docs-check.yml` lints markdown and warns about broken links, but nothing
verifies a documented number against the thing it documents. This does, so the
same drift cannot recur.

Design notes
------------
Counts are collected without *running* the suites: `pytest --collect-only`,
`vitest list`, and `playwright test --list` all enumerate without executing, so
this is cheap enough to sit in the fast gate tier.

A count that cannot be collected is reported as `skipped`, never as a mismatch.
A missing toolchain must not turn this red — a docs gate that fails because
node is absent teaches people to ignore it.

Usage:
    scripts/check_docs_sync.py            # human output, exit 1 on drift
    scripts/check_docs_sync.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "CI_CD.md"


def _run(cmd: list[str], cwd: Path, timeout: float = 300) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # See scripts/gates/ci-gate.py: a close_fds=True anywhere above a
            # node process makes it abort at teardown under PM2.
            close_fds=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def documented_counts() -> dict[str, int]:
    """Parse the canonical table: | Label | 1,234 | `cmd` |."""
    found: dict[str, int] = {}
    try:
        text = DOC.read_text()
    except OSError:
        return found
    for row in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*([\d,]+)\s*\|", text, re.MULTILINE):
        label, value = row.group(1).strip(), row.group(2).replace(",", "")
        if value.isdigit():
            found[label] = int(value)
    return found


def documented_workflow_count() -> int | None:
    try:
        m = re.search(r"with\s+(\d+)\s+workflows", DOC.read_text())
    except OSError:
        return None
    return int(m.group(1)) if m else None


def actual_backend_tests() -> int | None:
    rc, out = _run(
        ["uv", "run", "pytest", "tests/", "--collect-only", "-q"],
        REPO_ROOT / "backend",
        timeout=600,
    )
    m = re.search(r"(\d+)\s+tests? collected", out)
    return int(m.group(1)) if m else None


def actual_frontend_tests() -> int | None:
    """One line per test case.

    `vitest list --reporter=json` does not emit JSON in this version — the
    reporter flag is ignored for `list` and it prints
    "file > suite > case" lines. Count those rather than trusting the flag.
    """
    rc, out = _run(
        ["pnpm", "exec", "vitest", "list"],
        REPO_ROOT / "frontend",
        timeout=600,
    )
    lines = [
        line
        for line in out.splitlines()
        if " > " in line and line.strip() and not line.startswith(("$", ">", "EN"))
    ]
    return len(lines) or None


def actual_playwright_tests() -> int | None:
    rc, out = _run(
        ["pnpm", "exec", "playwright", "test", "--list"],
        REPO_ROOT / "frontend",
        timeout=600,
    )
    m = re.search(r"Total:\s+(\d+)\s+tests?", out)
    return int(m.group(1)) if m else None


def actual_workflow_count() -> int:
    wf = REPO_ROOT / ".github" / "workflows"
    return len([p for p in wf.iterdir() if p.suffix in (".yml", ".yaml")])


# Doc label -> collector. Labels must match the table's first column exactly.
COUNTED = {
    "Backend unit tests": actual_backend_tests,
    "Frontend unit tests": actual_frontend_tests,
    "Playwright E2E": actual_playwright_tests,
}


def build_report() -> dict:
    documented = documented_counts()
    results = []

    for label, collector in COUNTED.items():
        claimed = documented.get(label)
        if claimed is None:
            results.append(
                {
                    "name": label,
                    "status": "skipped",
                    "detail": "not present in the canonical table",
                }
            )
            continue
        actual = collector()
        if actual is None:
            results.append(
                {
                    "name": label,
                    "status": "skipped",
                    "detail": "could not collect (toolchain unavailable?)",
                    "documented": claimed,
                }
            )
            continue
        ok = actual == claimed
        results.append(
            {
                "name": label,
                "status": "ok" if ok else "drift",
                "documented": claimed,
                "actual": actual,
                "detail": (
                    f"{actual}" if ok else f"doc says {claimed:,}, actual is {actual:,}"
                ),
            }
        )

    claimed_wf = documented_workflow_count()
    actual_wf = actual_workflow_count()
    if claimed_wf is None:
        results.append(
            {
                "name": "Workflow count",
                "status": "skipped",
                "detail": "no 'with N workflows' sentence found",
            }
        )
    else:
        ok = claimed_wf == actual_wf
        results.append(
            {
                "name": "Workflow count",
                "status": "ok" if ok else "drift",
                "documented": claimed_wf,
                "actual": actual_wf,
                "detail": (
                    f"{actual_wf}"
                    if ok
                    else f"doc says {claimed_wf}, actual is {actual_wf}"
                ),
            }
        )

    drifted = [r for r in results if r["status"] == "drift"]
    return {
        "gate": "docs-sync",
        "doc": str(DOC.relative_to(REPO_ROOT)),
        "ok": not drifted,
        "checks": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {"ok": "OK  ", "drift": "DRIFT", "skipped": "skip"}
        print(f"docs sync ({report['doc']}): {'OK' if report['ok'] else 'DRIFT'}")
        for r in report["checks"]:
            print(f"  [{icon[r['status']]:<5}] {r['name']:<22} {r['detail']}")
        if not report["ok"]:
            print(
                "\nUpdate docs/CI_CD.md to match, or explain the difference there.",
                file=sys.stderr,
            )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
