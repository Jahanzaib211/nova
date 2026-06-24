"""Deterministic code-review builder (no LLM).

Produces a dual-audience review of a thread's workspace:

* a **plain-English** verdict for non-coders ("3 files changed, nothing risky, the
  app runs"), and
* a **developer** section (per-file +/- line stats, risk flags, detected checks).

Facts come from the host-side workspace (git when available, else a file scan) and
the per-thread ``sandbox.log`` audit trail — so the output is reliable and
reproducible. The richer prose pass is the separate ``/code-reviewer`` skill (LLM);
this module is the deterministic backbone the Review tab and the self-improving
loop both consume.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from deerflow.config.paths import get_paths

_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", ".cache", "dist", ".next", ".turbo"}
_REVIEW_FILENAME = "REVIEW.md"

# Risk patterns scanned across the audit trail (bash commands) and changed files.
_RISK_PATTERNS: list[tuple[str, str, str]] = [
    # (level, human message, regex)
    ("high", "Destructive delete (rm -rf)", r"\brm\s+-rf?\b"),
    ("high", "Pushes to a remote repo (git push)", r"\bgit\s+push\b"),
    ("high", "Pipes a remote script straight to a shell", r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b"),
    ("med", "Runs a database migration", r"\b(alembic|prisma\s+migrate|django-admin\s+migrate|knex\s+migrate|migrate\s+up)\b"),
    ("med", "Sends email / outbound message", r"\b(sendmail|smtplib|nodemailer|resend|sendgrid)\b"),
    # Require an actual deploy verb — a bare "vercel" matched "vercel-labs/skills"
    # (the `npx skills add` CLI) and produced a false positive.
    ("med", "Deploys to a hosting provider", r"\b(vercel\s+(deploy|--prod)|netlify\s+deploy|fly\s+deploy|gcloud\s+app\s+deploy|eb\s+deploy|wrangler\s+(deploy|publish))\b"),
]

# Secret-looking assignments inside changed files.
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|private[_-]?key)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]",
)
_PLACEHOLDER_RE = re.compile(r"(?i)(your[_-]?|example|placeholder|xxxx|<.*>|change[_-]?me|dummy|test)")


@dataclass
class FileChange:
    path: str
    added: int
    removed: int
    status: str  # added | modified | deleted | untracked

    def to_dict(self) -> dict:
        return {"path": self.path, "added": self.added, "removed": self.removed, "status": self.status}


@dataclass
class Risk:
    level: str  # high | med | low
    message: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "evidence": self.evidence}


@dataclass
class Review:
    files: list[FileChange] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)  # name -> ok|warn|fail|n/a
    added_total: int = 0
    removed_total: int = 0
    is_git: bool = False
    markdown: str = ""

    def to_dict(self) -> dict:
        return {
            "files": [f.to_dict() for f in self.files],
            "risks": [r.to_dict() for r in self.risks],
            "checks": self.checks,
            "added_total": self.added_total,
            "removed_total": self.removed_total,
            "is_git": self.is_git,
            "markdown": self.markdown,
        }


def _git(work_dir: Path, *args: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(work_dir), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return 1, str(e)


def _is_git_repo(work_dir: Path) -> bool:
    code, out = _git(work_dir, "rev-parse", "--is-inside-work-tree")
    return code == 0 and "true" in out


def _changes_from_git(work_dir: Path) -> list[FileChange]:
    """Compute per-file +/- vs HEAD, including untracked files (intent-to-add)."""
    # Stage intent-to-add so untracked files appear in the numstat without committing.
    _git(work_dir, "add", "-A", "-N")
    changes: dict[str, FileChange] = {}

    code, status = _git(work_dir, "status", "--porcelain")
    statuses: dict[str, str] = {}
    if code == 0:
        for line in status.splitlines():
            if len(line) < 4:
                continue
            code_xy, path = line[:2], line[3:].strip()
            if code_xy.strip() == "??" or "A" in code_xy:
                statuses[path] = "added"
            elif "D" in code_xy:
                statuses[path] = "deleted"
            else:
                statuses[path] = "modified"

    code, numstat = _git(work_dir, "diff", "HEAD", "--numstat")
    if code != 0:
        # No HEAD yet (fresh repo): diff against the empty tree.
        code, numstat = _git(work_dir, "diff", "--numstat", "--cached")
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_s, removed_s, path = parts
        if any(seg in _SKIP_DIRS for seg in path.split("/")):
            continue
        added = int(added_s) if added_s.isdigit() else 0
        removed = int(removed_s) if removed_s.isdigit() else 0
        changes[path] = FileChange(path=path, added=added, removed=removed, status=statuses.get(path, "modified"))

    # Include untracked/added files git counted as binary (-) or missed.
    for path, st in statuses.items():
        if path not in changes and not any(seg in _SKIP_DIRS for seg in path.split("/")):
            changes[path] = FileChange(path=path, added=0, removed=0, status=st)
    return sorted(changes.values(), key=lambda c: c.path)


def _changes_from_scan(work_dir: Path) -> list[FileChange]:
    """Fallback when not a git repo: every file is 'added' with its line count."""
    out: list[FileChange] = []
    for p in sorted(work_dir.rglob("*")):
        if p.is_dir():
            continue
        rel_parts = p.relative_to(work_dir).parts
        if any(seg.startswith(".") or seg in _SKIP_DIRS for seg in rel_parts):
            continue
        try:
            lines = sum(1 for _ in p.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            lines = 0
        out.append(FileChange(path=p.relative_to(work_dir).as_posix(), added=lines, removed=0, status="added"))
    return out


def _scan_audit_risks(thread_dir: Path) -> list[Risk]:
    risks: list[Risk] = []
    log_path = thread_dir / "sandbox.log"
    if not log_path.exists():
        return risks
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return risks
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        haystack = f"{ev.get('summary', '')} {ev.get('output', '')}"
        for level, msg, pattern in _RISK_PATTERNS:
            if msg in seen:
                continue
            if re.search(pattern, haystack):
                risks.append(Risk(level=level, message=msg, evidence=(ev.get("summary") or "")[:160]))
                seen.add(msg)
    return risks


def _scan_secret_risks(work_dir: Path, files: list[FileChange]) -> list[Risk]:
    risks: list[Risk] = []
    for fc in files:
        if fc.status == "deleted":
            continue
        p = work_dir / fc.path
        if not p.is_file() or p.stat().st_size > 512_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _SECRET_RE.finditer(text):
            snippet = m.group(0)
            if _PLACEHOLDER_RE.search(snippet):
                continue
            risks.append(Risk(level="high", message=f"Possible hard-coded secret in {fc.path}", evidence=snippet[:80]))
            break
    return risks


def _find_project_dir(work_dir: Path) -> Path:
    """Locate the runnable project root: the workspace itself, or the nearest
    immediate subdirectory holding package.json / pyproject (projects are often
    scaffolded one level deep, e.g. ``ground/``)."""
    if (work_dir / "package.json").exists() or (work_dir / "pyproject.toml").exists() or (work_dir / "requirements.txt").exists():
        return work_dir
    try:
        for child in sorted(work_dir.iterdir()):
            if child.is_dir() and child.name not in _SKIP_DIRS and not child.name.startswith("."):
                if (child / "package.json").exists() or (child / "pyproject.toml").exists() or (child / "requirements.txt").exists():
                    return child
    except OSError:
        pass
    return work_dir


def _detect_checks(work_dir: Path, files: list[FileChange]) -> dict[str, str]:
    checks: dict[str, str] = {}
    proj = _find_project_dir(work_dir)
    pkg = proj / "package.json"
    if pkg.exists():
        checks["node_project"] = "ok"
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {}) or {}
            checks["has_tests"] = "ok" if any(k in scripts for k in ("test", "test:unit")) else "warn"
            checks["has_build"] = "ok" if "build" in scripts else "warn"
            checks["runnable"] = "ok" if any(k in scripts for k in ("dev", "start", "serve")) else "warn"
        except Exception:
            pass
    if (proj / "requirements.txt").exists() or (proj / "pyproject.toml").exists():
        checks["python_project"] = "ok"
    if not (proj / ".gitignore").exists() and pkg.exists():
        checks["gitignore"] = "warn"
    return checks


def _render_markdown(rv: Review) -> str:
    high = [r for r in rv.risks if r.level == "high"]
    med = [r for r in rv.risks if r.level == "med"]
    n_files = len(rv.files)

    # Plain-English verdict (non-coder friendly).
    if high:
        verdict = "⚠️ Needs a look before you ship — some risky actions were detected."
    elif med:
        verdict = "🟡 Mostly fine — a couple of things worth a quick check."
    else:
        verdict = "✅ Looks clean — no risky actions detected."

    lines: list[str] = []
    lines.append("# Code Review")
    lines.append("")
    lines.append("## In plain English")
    lines.append("")
    lines.append(verdict)
    lines.append("")
    lines.append(
        f"- **{n_files}** file{'s' if n_files != 1 else ''} changed "
        f"(**+{rv.added_total}** lines added, **−{rv.removed_total}** removed)."
    )
    if high or med:
        lines.append(f"- **{len(high)}** high-risk and **{len(med)}** medium-risk item(s) to review (see below).")
    else:
        lines.append("- No destructive commands, secrets, deploys, or migrations were detected.")
    runs = rv.checks.get("has_build")
    if runs == "ok":
        lines.append("- The project has a build script, so it can be packaged for production.")
    if rv.checks.get("has_tests") == "warn":
        lines.append("- ℹ️ No automated tests were found — consider adding some.")
    lines.append("")

    # Developer section.
    lines.append("## For developers")
    lines.append("")
    if rv.risks:
        lines.append("### Risk flags")
        for r in rv.risks:
            tag = {"high": "🔴 HIGH", "med": "🟠 MED", "low": "🟡 LOW"}.get(r.level, r.level.upper())
            ev = f" — `{r.evidence}`" if r.evidence else ""
            lines.append(f"- {tag}: {r.message}{ev}")
        lines.append("")
    lines.append("### Changed files")
    if rv.files:
        lines.append("")
        lines.append("| File | Status | +/- |")
        lines.append("|------|--------|-----|")
        for f in rv.files[:200]:
            lines.append(f"| `{f.path}` | {f.status} | +{f.added} / −{f.removed} |")
    else:
        lines.append("_No changes detected._")
    lines.append("")
    if rv.checks:
        lines.append("### Detected checks")
        for name, state in sorted(rv.checks.items()):
            icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(state, "•")
            lines.append(f"- {icon} {name.replace('_', ' ')}: {state}")
        lines.append("")
    lines.append(f"_Source: {'git diff vs HEAD' if rv.is_git else 'workspace file scan'} + audit trail. Deterministic — no LLM._")
    return "\n".join(lines)


def build_review(thread_id: str, user_id: str | None = None, *, write_file: bool = True) -> Review:
    """Build a deterministic dual-audience review for a thread's workspace."""
    paths = get_paths()
    work_dir = paths.sandbox_work_dir(thread_id, user_id=user_id)
    thread_dir = paths.thread_dir(thread_id, user_id=user_id)

    rv = Review()
    if not work_dir.exists():
        rv.markdown = _render_markdown(rv)
        return rv

    rv.is_git = _is_git_repo(work_dir)
    rv.files = _changes_from_git(work_dir) if rv.is_git else _changes_from_scan(work_dir)
    rv.added_total = sum(f.added for f in rv.files)
    rv.removed_total = sum(f.removed for f in rv.files)
    rv.risks = _scan_audit_risks(thread_dir) + _scan_secret_risks(work_dir, rv.files)
    rv.checks = _detect_checks(work_dir, rv.files)
    rv.markdown = _render_markdown(rv)

    if write_file:
        try:
            (work_dir / _REVIEW_FILENAME).write_text(rv.markdown, encoding="utf-8")
        except OSError:
            pass
    return rv
