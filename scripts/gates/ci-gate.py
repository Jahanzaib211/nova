#!/usr/bin/env python3
"""CI / test / docs gate for the Nova Ops console.

Why this exists
---------------
Nova has 16 workflows and a dozen standalone check scripts, and none of them
produced a machine-readable verdict. `local-ci-gate` -- the only aggregate --
printed human text, and for months could not pass at all because it invoked a
`make format-check` target that did not exist. "CI is green" was unverifiable
from anywhere except a GitHub tab.

This runs the checks directly and emits one JSON document, so the console can
show a real per-check verdict and offer a Run button.

Tiers
-----
The checks are split by measured wall time, because a console that blocks for
eight minutes on a click is a console nobody clicks:

  fast (~40s total)  ruff check/format, tsc, eslint, prettier, vitest,
                     cross-ref, platform guardrails, blocking-io detector
  slow (minutes)     backend pytest (~7m40s), next build, playwright, lhci

Only the fast tier runs by default. `--tier slow` or `--tier all` opts in.

Every check reports native JSON where the tool supports it rather than
scraping human output.

Usage:
    scripts/gates/ci-gate.py                 # fast tier
    scripts/gates/ci-gate.py --tier all
    scripts/gates/ci-gate.py --only ruff,tsc
    scripts/gates/ci-gate.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"
GREEN, YELLOW, RED = "green", "yellow", "red"

FAST, SLOW = "fast", "slow"


def _status_path() -> Path:
    override = os.environ.get("NOVA_CI_GATE_STATUS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "gates" / "ci.json"


class Check:
    def __init__(
        self,
        name: str,
        tier: str,
        cwd: Path,
        cmd: list[str],
        timeout: float = 600,
        summarise=None,
    ):
        self.name = name
        self.tier = tier
        self.cwd = cwd
        self.cmd = cmd
        self.timeout = timeout
        self.summarise = summarise

    def run(self) -> dict:
        """Run the check, retrying once if the tool is killed by a signal.

        Under PM2 these tools intermittently die with SIGABRT *after* emitting
        complete, correct output -- the gate would show "0 errors" / "568
        passed" beside a RED verdict, with a matching core dump in frontend/.
        The cause is CPython's close_fds (see the note on close_fds=False
        below); this retry stays as a backstop in case a tool dies by signal
        for an unrelated reason.

        A signal death is retried once. A genuinely broken check
        fails twice and still reports RED; a teardown crash passes on the
        retry, so the gate stops crying wolf. A check that dies by signal twice
        is reported as such rather than silently swallowed.
        """
        result = self._run_once()
        rc = result.get("exit_code")
        if rc is not None and rc < 0:
            first = result
            result = self._run_once()
            result["retried_after_signal"] = -rc
            if result.get("exit_code") == 0:
                result["detail"] = f"{result['detail']} (retried after signal {-rc})"
            else:
                result["detail"] = f"{first['detail']} — died with signal {-rc} twice"
        return result

    def _run_once(self) -> dict:
        started = time.time()
        try:
            proc = subprocess.run(
                self.cmd,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                # close_fds=False is load-bearing, not an optimisation.
                #
                # Under PM2 these node tools died with SIGABRT *after* emitting
                # complete, correct output, so the gate reported "0 errors" /
                # "568 passed" beside a RED verdict, leaving a core dump in
                # frontend/ each time. Isolated under PM2 with three variants of
                # the identical `pnpm exec tsc --noEmit`:
                #
                #   stdout -> file                     exit 0
                #   stdout -> shell pipe               exit 0
                #   python subprocess, close_fds=True  exit -6  (SIGABRT)
                #   python subprocess, close_fds=False exit 0
                #
                # So it is CPython's descriptor-closing in the child, not PM2,
                # not pipes, and not RLIMIT_NOFILE -- clamping the limit in the
                # parent was measured and did NOT help. These are first-party
                # dev tools, and this process holds no sensitive descriptors, so
                # inheriting them is an acceptable trade for a gate that does
                # not fail at random.
                close_fds=False,
                env={
                    **os.environ,
                    # Backend tests import the app, which resolves config.yaml.
                    "PYTHONPATH": ".",
                    "CI": "1",
                },
            )
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return self._result(
                YELLOW,
                f"timed out after {self.timeout:.0f}s",
                time.time() - started,
                None,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._result(
                YELLOW, f"could not run: {exc}", time.time() - started, None
            )

        duration = time.time() - started
        detail = None
        if self.summarise:
            try:
                detail = self.summarise(rc, out, err)
            except Exception:  # noqa: BLE001 - summariser must not fail the gate
                detail = None
        if detail is None:
            tail = (err or out).strip().splitlines()
            detail = tail[-1][:200] if tail else ("passed" if rc == 0 else f"exit {rc}")

        result = self._result(GREEN if rc == 0 else RED, detail, duration, rc)
        if rc != 0:
            # A summariser reports what the tool *found*; it cannot explain a
            # tool that found nothing wrong and still exited non-zero (a crash,
            # an unhandled rejection after the suite passed, a missing binary).
            # Without the raw tail those cases are indistinguishable from a real
            # failure, so keep enough to tell them apart.
            result["output_tail"] = "\n".join((err or out).strip().splitlines()[-40:])[
                :4000
            ]
        return result

    def _result(
        self, status: str, detail: str, duration: float, rc: int | None
    ) -> dict:
        return {
            "name": self.name,
            "tier": self.tier,
            "status": status,
            "detail": detail,
            "duration_sec": round(duration, 2),
            "exit_code": rc,
            "command": " ".join(self.cmd),
        }


# ── summarisers: prefer each tool's native JSON over scraping text ──────────


def _ruff_check(rc, out, err):
    try:
        items = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    if not items:
        return "no violations"
    codes: dict[str, int] = {}
    for i in items:
        codes[i.get("code") or "?"] = codes.get(i.get("code") or "?", 0) + 1
    top = ", ".join(
        f"{c}×{n}" for c, n in sorted(codes.items(), key=lambda kv: -kv[1])[:4]
    )
    return f"{len(items)} violation(s): {top}"


def _ruff_format(rc, out, err):
    if rc == 0:
        return "all files formatted"
    n = len([l for l in out.splitlines() if l.strip()])
    return f"{n} file(s) would be reformatted"


def _eslint(rc, out, err):
    try:
        files = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    errors = sum(f.get("errorCount", 0) for f in files)
    warnings = sum(f.get("warningCount", 0) for f in files)
    return f"{errors} error(s), {warnings} warning(s)"


def _prettier(rc, out, err):
    if rc == 0:
        return "all files formatted"
    n = len([l for l in out.splitlines() if l.strip()])
    return f"{n} file(s) need formatting"


def _vitest(rc, out, err):
    for line in reversed((out + err).splitlines()):
        if "Tests" in line and ("passed" in line or "failed" in line):
            return line.strip()
    return None


def _pytest(rc, out, err):
    for line in reversed((out + err).splitlines()):
        if ("passed" in line or "failed" in line) and " in " in line:
            return line.strip().strip("= ")
    return None


def _tsc(rc, out, err):
    if rc == 0:
        return "no type errors"
    n = len([l for l in out.splitlines() if ": error TS" in l])
    return f"{n} type error(s)"


def build_checks() -> list[Check]:
    return [
        Check(
            "backend:ruff-check",
            FAST,
            BACKEND,
            ["uvx", "ruff@0.16.3", "check", ".", "--output-format", "json"],
            timeout=300,
            summarise=_ruff_check,
        ),
        Check(
            "backend:ruff-format",
            FAST,
            BACKEND,
            ["uvx", "ruff@0.16.3", "format", "--check", "."],
            timeout=300,
            summarise=_ruff_format,
        ),
        Check(
            "backend:cross-ref",
            FAST,
            REPO_ROOT,
            ["python3", "backend/tests/test_no_cross_references.py"],
            timeout=180,
        ),
        Check(
            "backend:guardrails",
            FAST,
            REPO_ROOT,
            ["python3", "scripts/check_platform_guardrails.py"],
            timeout=180,
        ),
        Check(
            "frontend:tsc",
            FAST,
            FRONTEND,
            ["pnpm", "exec", "tsc", "--noEmit"],
            timeout=600,
            summarise=_tsc,
        ),
        Check(
            "frontend:eslint",
            FAST,
            FRONTEND,
            ["pnpm", "exec", "eslint", ".", "--ext", ".ts,.tsx", "-f", "json"],
            timeout=600,
            summarise=_eslint,
        ),
        Check(
            "frontend:prettier",
            FAST,
            FRONTEND,
            # NB: --check and --list-different cannot be combined (prettier
            # errors "Cannot use --check and --list-different together"),
            # which made this gate fail while reporting 0 offending files.
            ["pnpm", "exec", "prettier", "--list-different", "."],
            timeout=400,
            summarise=_prettier,
        ),
        Check(
            "frontend:vitest",
            FAST,
            FRONTEND,
            ["pnpm", "exec", "vitest", "run"],
            timeout=600,
            summarise=_vitest,
        ),
        Check(
            "backend:pytest",
            SLOW,
            BACKEND,
            [
                "uv",
                "run",
                "pytest",
                "tests/",
                "-q",
                "--ignore=tests/test_sandbox_orphan_reconciliation_e2e.py",
            ],
            timeout=1800,
            summarise=_pytest,
        ),
        Check(
            "backend:blocking-io",
            SLOW,
            BACKEND,
            ["uv", "run", "pytest", "tests/blocking_io", "-q", "--tb=short"],
            timeout=900,
            summarise=_pytest,
        ),
        Check("frontend:build", SLOW, FRONTEND, ["pnpm", "build"], timeout=1800),
        # Collects three suites (--collect-only / list, never executing them),
        # so it is closer to a minute than a second — slow tier.
        Check(
            "docs:sync",
            SLOW,
            REPO_ROOT,
            ["python3", "scripts/check_docs_sync.py"],
            timeout=900,
        ),
        # The front<->back contract. contracts/*.json are loaded by BOTH sides'
        # tests, so a change to either that breaks the agreement fails here —
        # this is the gate that catches "nova apps not in sync", which nothing
        # else in the fast tier can see.
        Check(
            "contracts",
            FAST,
            BACKEND,
            [
                "uv",
                "run",
                "pytest",
                "tests/test_custom_events_contract.py",
                "tests/test_subagent_status_contract.py",
                "tests/test_browser_sdk_contract.py",
                "-q",
            ],
            timeout=600,
            summarise=_pytest,
        ),
        # Layer 1 of replay-e2e: asserts the backend's SSE event sequence still
        # matches a committed golden, with no API key and no model call. Layer 2
        # (the real-backend Playwright render) stays in CI — it needs two
        # servers and several minutes.
        Check(
            "replay:golden",
            SLOW,
            BACKEND,
            ["uv", "run", "pytest", "tests/test_replay_golden.py", "-q"],
            timeout=900,
            summarise=_pytest,
        ),
    ]


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".ci-gate-",
        suffix=".tmp",
        delete=False,
    ) as h:
        json.dump(payload, h, indent=2)
        h.flush()
        os.fsync(h.fileno())
        tmp = Path(h.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tier", choices=(FAST, SLOW, "all"), default=FAST)
    ap.add_argument(
        "--only", default="", help="comma-separated substrings of check names"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--status-path", type=Path, default=_status_path())
    args = ap.parse_args(argv)

    checks = build_checks()
    if args.tier != "all":
        checks = [c for c in checks if c.tier == args.tier]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        checks = [c for c in checks if any(w in c.name for w in wanted)]

    if not checks:
        print("ci-gate: no checks selected", file=sys.stderr)
        return 2

    started = time.time()
    results = []
    for check in checks:
        if not args.json:
            print(f"  running {check.name} ...", file=sys.stderr, flush=True)
        results.append(check.run())

    overall = (
        RED
        if any(r["status"] == RED for r in results)
        else (YELLOW if any(r["status"] == YELLOW for r in results) else GREEN)
    )
    report = {
        "gate": "ci",
        "checked_at_epoch": time.time(),
        "tier": args.tier,
        "duration_sec": round(time.time() - started, 1),
        "overall": overall,
        "ok": overall != RED,
        "checks": results,
    }

    # A partial run must not overwrite a full run's verdict for the other tier.
    if not args.print_only and not args.only:
        try:
            existing = {}
            if args.status_path.exists():
                existing = json.loads(args.status_path.read_text())
            merged = {r["name"]: r for r in existing.get("checks", [])}
            merged.update({r["name"]: r for r in results})
            report["checks"] = list(merged.values())
            report["overall"] = (
                RED
                if any(c["status"] == RED for c in report["checks"])
                else (
                    YELLOW
                    if any(c["status"] == YELLOW for c in report["checks"])
                    else GREEN
                )
            )
            report["ok"] = report["overall"] != RED
            write_atomic(args.status_path, report)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"ci-gate: could not write {args.status_path}: {exc}", file=sys.stderr
            )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(
            f"\nci gate ({args.tier}): {report['overall'].upper()} "
            f"in {report['duration_sec']}s"
        )
        for r in sorted(results, key=lambda x: x["name"]):
            print(
                f"  [{icon[r['status']]}] {r['name']:<26} {r['duration_sec']:>7.1f}s  {r['detail']}"
            )
    return 0 if overall != RED else 1


if __name__ == "__main__":
    raise SystemExit(main())
