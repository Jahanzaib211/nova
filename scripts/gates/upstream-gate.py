#!/usr/bin/env python3
"""Upstream isolation gate and patch miner.

Why this exists
---------------
Nova was forked from DeerFlow `v2.0.0-rc1` and then started over as its own
history: the two repositories share no commit. Upstream keeps shipping fixes
worth having, but its history must never enter Nova's. A `git pull upstream`,
a `merge --allow-unrelated-histories` or a rebase onto `upstream/main` would
drag in ~2,300 commits, resurrect files Nova deleted, and silently revert
Nova's changes to the ~500 files both sides touched.

So the rule is: mine upstream, never merge it. Fixes come across one commit at
a time as a *patch* on top of Nova's own history, carrying an `Upstream-Ref:`
trailer so provenance stays auditable.

How the gate tells the two apart
--------------------------------
Every commit's ancestry ends in a root commit. Nova's history has its own
root(s); upstream's has different ones. Merging or rebasing onto upstream
necessarily makes an upstream root reachable, so pinning Nova's roots catches
every way of importing upstream history -- offline, with no fetch, in
constant-size output. A ported patch is a new commit with a Nova parent, so
it never trips the check. (A shallow clone cannot see its real roots; the
gate refuses to vouch for one rather than pass it.)

Subcommands
-----------
    check           the gate: history roots, upstream push URL, gh default repo
    mine            list upstream commits since the fork, classified by whether
                    they apply to Nova cleanly, are already present, or conflict
    port SHA        apply one upstream commit to the index as a patch and write
                    a commit message with provenance trailers (never commits)
    skip SHA        record a decision not to port, so `mine` stops listing it
    install-hooks   commit-msg / pre-merge-commit / pre-push / pre-rebase hooks running `check`

Usage:
    scripts/gates/upstream-gate.py check [--json]
    scripts/gates/upstream-gate.py mine [--security] [--type fix] [--apply clean] [--limit 50] [--json | --markdown FILE]
    scripts/gates/upstream-gate.py port <sha> [--include-docs]
    scripts/gates/upstream-gate.py skip <sha> --reason "..."
    scripts/gates/upstream-gate.py install-hooks
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GREEN, YELLOW, RED = "green", "yellow", "red"

UPSTREAM_REPO = "bytedance/deer-flow"
UPSTREAM_URL_RE = re.compile(r"github\.com[:/]+bytedance/deer-flow(\.git)?/?$", re.IGNORECASE)

# Nova's own root commit(s). Adding one is a deliberate act that belongs in
# code review, which is why this is a constant and not configuration. The env
# override exists for the test suite, whose hook subprocesses cannot be
# monkeypatched.
NOVA_ROOTS = frozenset({"7bd76ec0545ae31734380cdb06f7199187c3ced8"})
ROOTS_ENV = "NOVA_UPSTREAM_GATE_ROOTS"

# `v2.0.0-rc1`, "Prepare 2.0.0 release" -- see NOVA_VS_DEERFLOW.md.
FORK_BASE = "98127f58456b254df96cc7da6f3f1b5f2528bef5"

LEDGER_PATH = "docs/upstream-ledger.json"
HOOK_MARKER = "nova-upstream-gate"
# commit-msg backs up pre-merge-commit: a merge that stops on conflicts is
# concluded by a plain `git commit`, which never runs pre-merge-commit.
HOOK_NAMES = ("commit-msg", "pre-merge-commit", "pre-push", "pre-rebase")
ZERO_SHA = "0" * 40

# Prose both projects rewrite independently. Upstream's edits to these never
# apply to Nova's copies and say nothing about whether the code change does.
DOC_PATTERNS = ("*CHANGELOG*.md", "*README*.md", "*AGENTS.md", "*CLAUDE.md")

SECURITY_RE = re.compile(
    r"\b(secur\w*|cve|ssrf|xss|csrf|inject\w*|travers\w*|leak\w*|credential\w*|auth\w*|sanitiz\w*|privilege\w*|escap\w*|vulnerab\w*|secret\w*|token\w*)\b",
    re.IGNORECASE,
)
CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?!?:")
PR_SUFFIX_RE = re.compile(r"\s*\(#(?P<num>\d+)\)\s*$")
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")


class GateError(RuntimeError):
    """A refusal the operator should read, not a crash."""


def _git(repo: Path, *args: str, stdin: str | None = None, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def _out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _allowed_roots() -> frozenset[str]:
    override = os.environ.get(ROOTS_ENV, "").strip()
    return frozenset(r.strip() for r in override.split(",") if r.strip()) if override else NOVA_ROOTS


def _check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def _worst(checks: list[dict]) -> str:
    states = {c["status"] for c in checks}
    return RED if RED in states else YELLOW if YELLOW in states else GREEN


# --- check ---------------------------------------------------------------


def parse_pre_push(stdin: str) -> list[str]:
    """Local SHAs a pre-push hook is about to send (deletions carry none)."""
    shas = []
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[1] != ZERO_SHA:
            shas.append(parts[1])
    return shas


def _merge_heads(repo: Path) -> list[str]:
    path = Path(_out(repo, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD"))
    return path.read_text().split() if path.exists() else []


def check_history_roots(repo: Path, revs: list[str]) -> dict:
    if _out(repo, "rev-parse", "--is-shallow-repository") == "true":
        return _check(
            "history_roots",
            RED,
            "shallow clone: its roots are the clone boundary, not the real history, so isolation cannot be proven (CI: checkout with fetch-depth: 0)",
        )
    if not revs:
        return _check("history_roots", GREEN, "nothing to inspect")
    allowed = _allowed_roots()
    roots = set(_out(repo, "rev-list", "--max-parents=0", *revs).split())
    foreign = sorted(roots - allowed)
    if foreign:
        return _check(
            "history_roots",
            RED,
            f"foreign root commit(s) {', '.join(r[:12] for r in foreign)} reachable from {', '.join(revs)} -- upstream history was merged or rebased in; port patches with `upstream-gate.py port` instead",
            foreign_roots=foreign,
        )
    return _check("history_roots", GREEN, f"only Nova's own root reachable from {', '.join(revs)}")


def _upstream_remotes(repo: Path) -> list[str]:
    names = _out(repo, "remote").split()
    return [n for n in names if UPSTREAM_URL_RE.search(_out(repo, "remote", "get-url", n))]


def check_push_disabled(repo: Path) -> dict:
    remotes = _upstream_remotes(repo)
    if not remotes:
        return _check("upstream_push_disabled", GREEN, "no upstream remote configured")
    pushable = [n for n in remotes if UPSTREAM_URL_RE.search(_out(repo, "remote", "get-url", "--push", n))]
    if pushable:
        return _check(
            "upstream_push_disabled",
            RED,
            f"remote(s) {', '.join(pushable)} can push to {UPSTREAM_REPO}; run `git remote set-url --push {pushable[0]} DISABLED`",
        )
    return _check("upstream_push_disabled", GREEN, f"remote(s) {', '.join(remotes)} are fetch-only")


def check_gh_default(repo: Path) -> dict:
    # `gh repo set-default` records its choice as remote.<name>.gh-resolved.
    resolved = [n for n in _upstream_remotes(repo) if _git(repo, "config", "--get", f"remote.{n}.gh-resolved", check=False).stdout.strip()]
    if resolved:
        return _check(
            "gh_default_repo",
            RED,
            f"gh resolves to {UPSTREAM_REPO} via remote {resolved[0]}, so `gh pr create` and `gh run list` act on upstream; run `gh repo set-default <your fork>`",
        )
    return _check("gh_default_repo", GREEN, "gh does not default to upstream")


def check_push_target(url: str) -> dict:
    if UPSTREAM_URL_RE.search(url):
        return _check("push_target", RED, f"refusing to push to {UPSTREAM_REPO} ({url})")
    return _check("push_target", GREEN, f"push target {url} is not upstream")


def build_check_report(
    repo: Path,
    *,
    pushed: list[str] | None = None,
    push_url: str | None = None,
    incoming: list[str] | None = None,
    include_merge_head: bool = False,
) -> dict:
    """Run the gate. With hook context only the commits in flight are inspected."""
    hook_context = pushed is not None or push_url is not None or incoming is not None or include_merge_head
    revs = list(pushed or []) + list(incoming or [])
    if include_merge_head:
        revs += _merge_heads(repo)
    if not hook_context:
        revs = ["HEAD"]
    checks = [check_history_roots(repo, revs)]
    if push_url is not None:
        checks.append(check_push_target(push_url))
    checks += [check_push_disabled(repo), check_gh_default(repo)]
    return {
        "gate": "upstream",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "status": _worst(checks),
        "checks": checks,
    }


# --- mine ----------------------------------------------------------------


def _require_ref(repo: Path, ref: str) -> str:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if proc.returncode != 0:
        raise GateError(f"{ref} is not available. Add a fetch-only remote and fetch it first:\n  git remote add upstream https://github.com/{UPSTREAM_REPO}.git\n  git remote set-url --push upstream DISABLED\n  git fetch upstream")
    return proc.stdout.strip()


def ported_shas(repo: Path) -> set[str]:
    trailers = _out(repo, "log", "--format=%(trailers:key=Upstream-Ref,valueonly,separator=%x20)", "HEAD")
    return set(SHA_RE.findall(trailers))


def _ledger_file(repo: Path) -> Path:
    return repo / LEDGER_PATH


def load_ledger(repo: Path) -> dict:
    path = _ledger_file(repo)
    if not path.exists():
        return {"version": 1, "skipped": {}}
    return json.loads(path.read_text())


def record_skip(repo: Path, sha: str, reason: str) -> dict:
    full = _out(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    ledger = load_ledger(repo)
    ledger.setdefault("skipped", {})[full] = {
        "subject": _out(repo, "log", "-1", "--format=%s", full),
        "reason": reason,
        "date": _dt.date.today().isoformat(),
    }
    ledger["skipped"] = dict(sorted(ledger["skipped"].items()))
    path = _ledger_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    return ledger["skipped"][full]


def _is_doc(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in DOC_PATTERNS)


def _commit_files(repo: Path, sha: str) -> list[str]:
    return [f for f in _out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", sha).splitlines() if f]


def _patch(repo: Path, sha: str, files: list[str]) -> str:
    return _git(repo, "diff", "--binary", "--no-renames", f"{sha}^", sha, "--", *files).stdout


def _applies(repo: Path, patch: str, index_env: dict[str, str], *, reverse: bool = False) -> bool:
    args = ["apply", "--cached", "--check", *(["-R"] if reverse else [])]
    return _git(repo, *args, stdin=patch, env=index_env, check=False).returncode == 0


def _classify(repo: Path, sha: str, index_env: dict[str, str]) -> dict:
    files = _commit_files(repo, sha)
    code = [f for f in files if not _is_doc(f)]
    if not code:
        return {"apply": "docs-only", "files": files}
    patch = _patch(repo, sha, code)
    if _applies(repo, patch, index_env):
        state = "clean"
    elif _applies(repo, patch, index_env, reverse=True):
        state = "present"
    else:
        state = "conflict"
    return {"apply": state, "files": code}


def mine(repo: Path, *, ref: str = "upstream/main", check_apply: bool = True, jobs: int = 8) -> list[dict]:
    """Upstream commits since the fork that Nova has neither ported nor skipped, newest first."""
    _require_ref(repo, ref)
    log = _out(repo, "log", "--no-merges", "--format=%H%x1f%cs%x1f%an%x1f%s%x1e", f"{FORK_BASE}..{ref}")
    done = ported_shas(repo) | set(load_ledger(repo).get("skipped", {}))
    rows = []
    for record in log.split("\x1e"):
        if not record.strip():
            continue
        sha, date, author, subject = record.strip().split("\x1f")
        if sha in done:
            continue
        match = CONVENTIONAL_RE.match(subject)
        rows.append(
            {
                "sha": sha,
                "date": date,
                "author": author,
                "subject": subject,
                "type": match.group("type") if match else "other",
                "security": bool(SECURITY_RE.search(subject)),
            }
        )
    if not check_apply:
        return rows

    nova_changed = set(_out(repo, "diff", "--name-only", "--no-renames", FORK_BASE, "HEAD").splitlines())
    with tempfile.TemporaryDirectory() as tmp:
        # A private index seeded from HEAD: the check is independent of the
        # working tree, and `apply --check` only reads it, so workers can share.
        index_env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, "read-tree", "HEAD", env=index_env)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(lambda row: _classify(repo, row["sha"], index_env), rows))
    for row, result in zip(rows, results):
        row["apply"] = result["apply"]
        row["files"] = result["files"]
        row["nova_modified"] = sorted(f for f in result["files"] if f in nova_changed)
    return rows


def _filter(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.security:
        rows = [r for r in rows if r["security"]]
    if args.type:
        rows = [r for r in rows if r["type"] == args.type]
    if args.apply:
        rows = [r for r in rows if r.get("apply") == args.apply]
    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        rows = [r for r in rows if pattern.search(r["subject"])]
    return rows[: args.limit] if args.limit else rows


def _summary(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("apply", "unchecked")] = counts.get(row.get("apply", "unchecked"), 0) + 1
    return counts


def _markdown(rows: list[dict], ref: str) -> str:
    lines = [
        f"# Upstream mining report ({UPSTREAM_REPO}, `{FORK_BASE[:9]}..{ref}`)",
        "",
        f"Generated {_dt.date.today().isoformat()} by `scripts/gates/upstream-gate.py mine`. Excludes commits already ported (`Upstream-Ref:` trailer) or skipped in the ledger.",
        "",
        "| Status | Count |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in sorted(_summary(rows).items())),
        "",
        "| Commit | Date | Apply | Sec | Nova-modified files | Subject |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        subject = r["subject"].replace("|", "\\|")
        touched = ", ".join(f"`{f}`" for f in r.get("nova_modified", [])[:3]) + (" …" if len(r.get("nova_modified", [])) > 3 else "")
        lines.append(f"| `{r['sha'][:9]}` | {r['date']} | {r.get('apply', '')} | {'yes' if r['security'] else ''} | {touched} | {subject} |")
    return "\n".join(lines) + "\n"


# --- port ----------------------------------------------------------------


def _message(repo: Path, sha: str) -> str:
    subject = _out(repo, "log", "-1", "--format=%s", sha)
    body = _out(repo, "log", "-1", "--format=%b", sha)
    author = _out(repo, "log", "-1", "--format=%an", sha)
    trailers = [f"Upstream-Ref: {UPSTREAM_REPO}@{sha}"]
    pr = PR_SUFFIX_RE.search(subject)
    if pr:
        # Left in the subject, GitHub would link "#7001" to Nova's own PR #7001.
        subject = subject[: pr.start()]
        trailers.append(f"Upstream-PR: {UPSTREAM_REPO}#{pr.group('num')}")
    trailers.append(f"Upstream-Author: {author}")
    parts = [subject]
    if body:
        parts.append(body)
    parts.append("\n".join(trailers))
    return "\n\n".join(parts) + "\n"


def port(repo: Path, sha: str, *, ref: str = "upstream/main", include_docs: bool = False) -> dict:
    """Stage one upstream commit as a patch. The operator tests and commits."""
    _require_ref(repo, ref)
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
    full = proc.stdout.strip()
    is_upstream = proc.returncode == 0 and full != FORK_BASE and _git(repo, "merge-base", "--is-ancestor", FORK_BASE, full, check=False).returncode == 0 and _git(repo, "merge-base", "--is-ancestor", full, ref, check=False).returncode == 0
    if not is_upstream:
        raise GateError(f"{sha} is not an upstream commit after the fork base ({FORK_BASE[:9]}..{ref})")
    if full in ported_shas(repo):
        raise GateError(f"{full[:12]} is already ported (an Upstream-Ref trailer names it)")
    if _out(repo, "status", "--porcelain", "--untracked-files=no"):
        raise GateError("the working tree has uncommitted changes; commit or stash them so the port is reviewable on its own")

    files = _commit_files(repo, full)
    keep = files if include_docs else [f for f in files if not _is_doc(f)]
    if not keep:
        raise GateError(f"{full[:12]} only touches docs Nova maintains separately; pass --include-docs to take them anyway")
    applied = _git(repo, "apply", "--3way", "--whitespace=nowarn", stdin=_patch(repo, full, keep), check=False)
    conflicts = [f for f in _out(repo, "diff", "--name-only", "--diff-filter=U").splitlines() if f]
    if applied.returncode != 0 and not conflicts:
        raise GateError(f"patch did not apply: {applied.stderr.strip()}")

    message_file = Path(_out(repo, "rev-parse", "--path-format=absolute", "--git-path", "UPSTREAM_PORT_MSG"))
    message_file.write_text(_message(repo, full))
    return {
        "sha": full,
        "files": keep,
        "skipped_docs": sorted(set(files) - set(keep)),
        "conflicts": conflicts,
        "message_file": str(message_file),
    }


# --- hooks ---------------------------------------------------------------


def _hook_body(name: str) -> str:
    script = Path(__file__).resolve()
    return f'#!/bin/sh\n# {HOOK_MARKER}: installed by scripts/gates/upstream-gate.py install-hooks\nexec python3 "{script}" check --hook {name} "$@"\n'


def install_hooks(repo: Path) -> dict:
    hooks_dir = Path(_out(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hooks_dir.mkdir(parents=True, exist_ok=True)
    installed, skipped = [], []
    for name in HOOK_NAMES:
        path = hooks_dir / name
        if path.exists() and HOOK_MARKER not in path.read_text(errors="replace"):
            skipped.append(name)
            continue
        path.write_text(_hook_body(name))
        path.chmod(0o755)
        installed.append(name)
    return {"hooks_dir": str(hooks_dir), "installed": sorted(installed), "skipped": sorted(skipped)}


def _hook_report(repo: Path, hook: str, hook_args: list[str]) -> dict:
    if hook == "pre-push":
        url = hook_args[1] if len(hook_args) > 1 else ""
        return build_check_report(repo, pushed=parse_pre_push(sys.stdin.read()), push_url=url)
    if hook in ("pre-merge-commit", "commit-msg"):
        return build_check_report(repo, include_merge_head=True)
    if hook == "pre-rebase":
        # git passes pre-rebase only <upstream> (and the branch, when it is
        # not the current one) -- never the `--onto` target, and no rebase
        # state exists on disk yet (rebase-merge/onto is written afterwards;
        # verified on git 2.53). So `git rebase --onto upstream/main <base>`
        # is invisible in the hook's arguments. The cheap detection is the
        # parent git process's own command line; where that cannot be read
        # (no /proc), pre-push remains the enforcement point.
        upstream = hook_args[0] if hook_args else "@{upstream}"
        candidates = [upstream]
        onto = parse_rebase_onto(_parent_cmdline())
        if onto:
            candidates.append(onto)
        incoming = [ref for ref in candidates if _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0]
        report = build_check_report(repo, incoming=incoming)
        for c in report["checks"]:
            if c["name"] == "history_roots" and c["status"] != RED:
                seen = "the --onto target and <upstream>" if onto else "<upstream> only (no --onto target visible)"
                c["detail"] += f" [pre-rebase inspected {seen}; pre-push is the enforcement point]"
        return report
    raise GateError(f"unknown hook {hook}")


def parse_rebase_onto(argv: list[str]) -> str | None:
    """The `--onto` target of a `git ... rebase ...` command line, or None."""
    if "rebase" not in argv:
        return None
    args = argv[argv.index("rebase") + 1 :]
    for i, arg in enumerate(args):
        if arg.startswith("--onto="):
            return arg.split("=", 1)[1] or None
        if arg == "--onto":
            return args[i + 1] if i + 1 < len(args) else None
    return None


def _parent_cmdline() -> list[str]:
    """argv of the git process running this hook; [] where unreadable.

    The hook body `exec`s python, so the parent is git itself. Linux-only
    (/proc); elsewhere this returns [] and the hook degrades to inspecting
    <upstream> alone.
    """
    try:
        raw = Path(f"/proc/{os.getppid()}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


# --- CLI -----------------------------------------------------------------


def _print_report(report: dict) -> None:
    print(f"upstream gate: {report['status'].upper()}")
    for c in report["checks"]:
        print(f"  [{c['status']:>6}] {c['name']}: {c['detail']}")


def _repo() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="run the isolation gate")
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--hook", choices=HOOK_NAMES, help=argparse.SUPPRESS)
    p_check.add_argument("hook_args", nargs="*", help=argparse.SUPPRESS)

    p_mine = sub.add_parser("mine", help="list portable upstream commits")
    p_mine.add_argument("--ref", default="upstream/main")
    p_mine.add_argument("--security", action="store_true", help="only commits whose subject reads as security-relevant")
    p_mine.add_argument("--type", help="conventional-commit type, e.g. fix, feat, perf")
    p_mine.add_argument("--apply", choices=("clean", "present", "conflict", "docs-only"))
    p_mine.add_argument("--grep", help="regex over the subject")
    p_mine.add_argument("--limit", type=int, default=0)
    p_mine.add_argument("--no-apply-check", action="store_true", help="skip the (slower) applicability check")
    out = p_mine.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true")
    out.add_argument("--markdown", metavar="FILE")

    p_port = sub.add_parser("port", help="stage one upstream commit as a patch")
    p_port.add_argument("sha")
    p_port.add_argument("--ref", default="upstream/main")
    p_port.add_argument("--include-docs", action="store_true")

    p_skip = sub.add_parser("skip", help="record that an upstream commit will not be ported")
    p_skip.add_argument("sha")
    p_skip.add_argument("--reason", required=True)

    sub.add_parser("install-hooks", help="install commit-msg / pre-merge-commit / pre-push / pre-rebase hooks")

    args = parser.parse_args(argv)
    repo = _repo()
    try:
        if args.cmd == "check":
            report = _hook_report(repo, args.hook, args.hook_args) if args.hook else build_check_report(repo)
            if args.json:
                print(json.dumps(report, indent=2))
            elif args.hook:
                # Hooks stay quiet unless they block something.
                if report["status"] == RED:
                    _print_report(report)
            else:
                _print_report(report)
            if args.hook:
                # In a hook only the history and push target block; config
                # findings are reported by a plain `check`.
                return 1 if any(c["status"] == RED and c["name"] in ("history_roots", "push_target") for c in report["checks"]) else 0
            return 1 if report["status"] == RED else 0

        if args.cmd == "mine":
            rows = _filter(mine(repo, ref=args.ref, check_apply=not args.no_apply_check), args)
            if args.json:
                print(json.dumps({"ref": args.ref, "fork_base": FORK_BASE, "summary": _summary(rows), "commits": rows}, indent=2))
            elif args.markdown:
                Path(args.markdown).write_text(_markdown(rows, args.ref))
                print(f"wrote {len(rows)} commits to {args.markdown}: {_summary(rows)}")
            else:
                for r in rows:
                    flag = "SEC" if r["security"] else "   "
                    print(f"{r['sha'][:9]}  {r['date']}  {r.get('apply', '?'):<9} {flag}  {r['subject']}")
                print(f"\n{len(rows)} commits: {_summary(rows)}", file=sys.stderr)
            return 0

        if args.cmd == "port":
            result = port(repo, args.sha, ref=args.ref, include_docs=args.include_docs)
            print(f"staged {len(result['files'])} file(s) from {UPSTREAM_REPO}@{result['sha'][:12]}")
            if result["skipped_docs"]:
                print(f"  left out docs Nova maintains itself: {', '.join(result['skipped_docs'])}")
            if result["conflicts"]:
                print(f"  CONFLICTS to resolve: {', '.join(result['conflicts'])}")
            print(f"run the tests, then: git commit -e -F {result['message_file']}")
            return 2 if result["conflicts"] else 0

        if args.cmd == "skip":
            entry = record_skip(repo, args.sha, args.reason)
            print(f"recorded skip in {LEDGER_PATH}: {entry['subject']}")
            return 0

        if args.cmd == "install-hooks":
            result = install_hooks(repo)
            print(f"installed {', '.join(result['installed']) or 'nothing'} in {result['hooks_dir']}")
            if result["skipped"]:
                print(f"  left existing hooks alone: {', '.join(result['skipped'])} (add a call to this gate by hand)")
            return 0
    except GateError as exc:
        print(f"upstream gate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
