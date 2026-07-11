"""Auto-fix tool for cross-project reference violations in nova.

Reads violations from `backend/tests/test_no_cross_references.py` and
applies safe one-to-one renames. Designed as a quick first pass for AI
coding sessions — handles the common patterns; deep refactors remain
manual.

Usage:
    python3 scripts/fix_cross_references.py --dry-run   # preview
    python3 scripts/fix_cross_references.py             # apply
    python3 scripts/fix_cross_references.py --path <file>   # limit to one file

Safety rules:
  - Never touches .md files (docs are human-decision territory)
  - Never touches files in the allow-list
  - Writes via temp file + rename (atomic)
  - Won't operate outside repo root

Exit codes:
  0 — nothing to fix OR fixes applied successfully
  1 — fixes applied but post-fix verification failed (review needed)
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Safe one-to-one renames. Longer patterns first so they win.
RENAMES: list[tuple[str, str, str]] = [
    # Most specific first (env-var renames, if any)
]


def _is_md(path: Path) -> bool:
    return path.suffix.lower() == ".md"


def _is_in_allow_list(rel: str) -> bool:
    sys.path.insert(0, str(REPO_ROOT / "backend/tests"))
    import test_no_cross_references as t

    return any(rel == allowed_path for allowed_path, _, _ in t.ALLOW_LIST)


def _find_violations() -> list[tuple[Path, int, str, str]]:
    sys.path.insert(0, str(REPO_ROOT / "backend/tests"))
    import test_no_cross_references as t

    files = t._gather_files()
    out = []
    for path in files:
        for lineno, forbidden, line in t._scan_file(path):
            out.append((path, lineno, forbidden, line))
    return out


def _human_review_strings() -> set[str]:
    """The set of forbidden strings that the auto-fix tool should NOT attempt
    to rewrite. Sourced from the canonical FORBIDDEN list in the test module
    so there's a single source of truth."""
    sys.path.insert(0, str(REPO_ROOT / "backend/tests"))
    import test_no_cross_references as t

    return {f.lower() for f in t.FORBIDDEN}


def _plan_fix(path: Path, lineno: int, forbidden: str, line: str) -> ProposedFix | None:
    """Decide whether/how to fix one violation."""
    rel = str(path.relative_to(REPO_ROOT))

    if _is_md(path):
        return None
    if _is_in_allow_list(rel):
        return None

    # First: scan line for any RENAMES pattern
    for old, new, desc in RENAMES:
        if old in line:
            new_line = re.sub(re.escape(old), new, line, count=1)
            return ProposedFix(
                path=path,
                line_no=lineno,
                before=line,
                after=new_line,
                description=f"{desc} (renamed {old!r} → {new!r})",
            )

    # No specific rename pattern. Defer to human review for bare strings.
    if forbidden.lower() in _human_review_strings():
        return None

    return None


@dataclass
class ProposedFix:
    path: Path
    line_no: int
    before: str
    after: str
    description: str


def _apply_fix(fix: ProposedFix) -> bool:
    content = fix.path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    if fix.line_no < 1 or fix.line_no > len(lines):
        return False
    old_line = lines[fix.line_no - 1]
    new_line = old_line.replace(fix.before, fix.after)
    if new_line == old_line:
        return False
    lines[fix.line_no - 1] = new_line
    fd, tmp_path = tempfile.mkstemp(dir=fix.path.parent, prefix=".fix.", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
        Path(tmp_path).rename(fix.path)
        return True
    except Exception:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        return False


def _verify_no_cross_refs() -> bool:
    """Run the cross-ref test to verify fixes didn't break anything."""
    import subprocess

    result = subprocess.run(
        ["python3", str(REPO_ROOT / "backend/tests/test_no_cross_references.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Post-fix cross-ref verification FAILED:")
        print(result.stdout[-1500:])
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only; don't write"
    )
    parser.add_argument(
        "--path", type=Path, help="Limit fixes to this path (relative to repo root)"
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="Skip post-fix verification"
    )
    args = parser.parse_args()

    print(f"Scanning {REPO_ROOT} for cross-project violations...")
    print()
    violations = _find_violations()

    if args.path:
        violations = [v for v in violations if v[0].relative_to(REPO_ROOT) == args.path]

    if not violations:
        print("Nothing to fix.")
        return 0

    print(f"Found {len(violations)} violation(s). Planning fixes...")
    print()

    fixes: list[ProposedFix] = []
    needs_human_review: list[tuple[Path, int, str, str]] = []

    for path, lineno, forbidden, line in violations:
        fix = _plan_fix(path, lineno, forbidden, line)
        if fix is not None:
            fixes.append(fix)
        else:
            needs_human_review.append((path, lineno, forbidden, line))

    for fix in fixes:
        rel = fix.path.relative_to(REPO_ROOT)
        print(f"  {rel}:{fix.line_no}  {fix.description}")
        print(f"    - {fix.before.strip()[:80]}")
        print(f"    + {fix.after.strip()[:80]}")

    if not fixes:
        print(f"\nNo auto-fixable patterns found in {len(violations)} violation(s).")
        if needs_human_review:
            print(f"\nHuman review needed for {len(needs_human_review)} item(s):")
            for path, lineno, forbidden, line in needs_human_review[:10]:
                rel = path.relative_to(REPO_ROOT)
                print(f"  {rel}:{lineno}  {forbidden}: {line.strip()[:80]}")
            if len(needs_human_review) > 10:
                print(f"  ... and {len(needs_human_review) - 10} more")
        return 0

    print(f"\nPlanned {len(fixes)} fix(es).")

    if args.dry_run:
        print("Dry-run mode — no changes written.")
        return 0

    success_count = 0
    failure_count = 0
    for fix in fixes:
        if _apply_fix(fix):
            success_count += 1
            print(f"  ✓ {fix.path.relative_to(REPO_ROOT)}:{fix.line_no}")
        else:
            failure_count += 1
            print(f"  ✗ FAILED {fix.path.relative_to(REPO_ROOT)}:{fix.line_no}")

    print()
    print(f"Applied {success_count} fix(es); {failure_count} failure(s).")

    if not args.skip_verify:
        if not _verify_no_cross_refs():
            print("Post-fix verification FAILED — review the changes.")
            return 1
        print("Post-fix cross-ref check: OK")

    if needs_human_review:
        print(
            f"\nHuman review needed for {len(needs_human_review)} item(s) not auto-fixed:"
        )
        for path, lineno, forbidden, line in needs_human_review[:10]:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{lineno}  {forbidden}: {line.strip()[:80]}")
        if len(needs_human_review) > 10:
            print(f"  ... and {len(needs_human_review) - 10} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
