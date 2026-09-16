#!/usr/bin/env python3
"""Fold Lighthouse CI results into a Nova Ops gate.

Why this exists
---------------
lighthouserc.cjs, the workflow and @lhci/cli are all in place, but `lhci`
writes its output to frontend/.lighthouseci/ in its own format. Nothing turned
that into a gate, so performance and accessibility scores existed in CI
artifacts and nowhere an operator would look.

This reads the most recent run and republishes it in the same shape as the
other gates (~/.nova/gates/lighthouse.json), so one console component renders
all of them.

It deliberately does NOT run Lighthouse itself. A full run needs a production
build plus a real Chrome and takes minutes; wiring that into a polling daemon
would make a background job that occasionally saturates the box. `--run`
exists for an explicit, operator-initiated run (the console's Run button); the
default path only reports what CI or a previous manual run already produced.

Usage:
    scripts/gates/lighthouse-gate.py           # publish the latest results
    scripts/gates/lighthouse-gate.py --run     # run lhci first (slow)
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
FRONTEND = REPO_ROOT / "frontend"
LHCI_DIR = FRONTEND / ".lighthouseci"
GREEN, YELLOW, RED = "green", "yellow", "red"

# How old the newest Lighthouse report may be before the gate stops calling
# itself green. Runs are CI- or operator-triggered, so a couple of days is
# normal; a week means nothing is measuring this any more.
STALE_YELLOW_DAYS = float(os.environ.get("NOVA_LIGHTHOUSE_STALE_YELLOW_DAYS", "3"))
STALE_RED_DAYS = float(os.environ.get("NOVA_LIGHTHOUSE_STALE_RED_DAYS", "7"))

# Mirrors the assertions in frontend/lighthouserc.cjs. Accessibility is the one
# hard requirement there, so it is the one that can go red here; the rest warn,
# to be ratcheted up as the numbers improve rather than being red on day one.
THRESHOLDS = {
    "performance": (0.50, False),
    "accessibility": (0.85, True),
    "best-practices": (0.80, False),
    "seo": (0.80, False),
}


def _status_path() -> Path:
    override = os.environ.get("NOVA_LIGHTHOUSE_GATE_STATUS_PATH", "").strip()
    return (
        Path(override)
        if override
        else Path.home() / ".nova" / "gates" / "lighthouse.json"
    )


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def run_lhci() -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["pnpm", "exec", "lhci", "autorun", "--config=./lighthouserc.cjs"],
            cwd=FRONTEND,
            capture_output=True,
            text=True,
            timeout=1800,
            close_fds=False,  # see scripts/gates/ci-gate.py
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "lhci autorun exceeded 30 minutes"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"could not run lhci: {exc}"


def latest_reports() -> list[Path]:
    if not LHCI_DIR.is_dir():
        return []
    return sorted(
        LHCI_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )


def parse_report(path: Path) -> dict | None:
    """Extract category scores from one lhr JSON. Returns None if not an lhr."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    # .lighthouseci/ also holds assertion-results.json (a list) and
    # manifest.json (a list), not just lhr reports. Anything that is not a
    # dict with categories is simply not a report.
    if not isinstance(data, dict):
        return None
    categories = data.get("categories")
    if not isinstance(categories, dict) or "performance" not in categories:
        return None  # manifest.json / assertion-results.json, not a report
    return {
        "url": data.get("finalDisplayedUrl") or data.get("requestedUrl") or "?",
        "fetched_at": data.get("fetchTime"),
        "scores": {
            key: (value or {}).get("score") for key, value in categories.items()
        },
    }


def build_report() -> dict:
    reports = [r for r in (parse_report(p) for p in latest_reports()) if r]

    if not reports:
        return {
            "gate": "lighthouse",
            "checked_at_epoch": time.time(),
            "overall": YELLOW,
            "ok": True,
            "checks": [
                check(
                    "lighthouse",
                    YELLOW,
                    "no results yet — run `pnpm lighthouse` in frontend/, "
                    "or trigger this gate with --run",
                )
            ],
        }

    # One run produces a report per URL; keep the worst score per category so a
    # single bad page cannot hide behind a good one.
    worst: dict[str, float] = {}
    for report in reports:
        for key, score in report["scores"].items():
            if score is None:
                continue
            worst[key] = min(worst.get(key, 1.0), float(score))

    checks = []
    for key, (threshold, hard) in THRESHOLDS.items():
        score = worst.get(key)
        if score is None:
            checks.append(check(key, YELLOW, "not measured"))
            continue
        pct = score * 100
        if score >= threshold:
            checks.append(
                check(
                    key,
                    GREEN,
                    f"{pct:.0f} (min {threshold * 100:.0f})",
                    score=round(score, 3),
                )
            )
        else:
            checks.append(
                check(
                    key,
                    RED if hard else YELLOW,
                    f"{pct:.0f} is below the {threshold * 100:.0f} budget",
                    score=round(score, 3),
                )
            )

    newest = reports[0]
    # Age is part of the verdict, not decoration.
    #
    # This gate republishes whatever the last lhci run produced; it deliberately
    # does not run Lighthouse itself (see the module docstring). That is the
    # right call, but it means the scores above are only as true as the run that
    # produced them -- and with nothing scheduling runs, this gate sat showing
    # four green budgets from a 9-day-old measurement of a build that no longer
    # existed. A gate that reports green while measuring nothing is worse than
    # one that reports red, because it is trusted.
    age_days = _report_age_days(newest)
    if age_days is None:
        status, age_note = YELLOW, "age unknown"
    elif age_days >= STALE_RED_DAYS:
        status, age_note = RED, f"{age_days:.1f}d old"
    elif age_days >= STALE_YELLOW_DAYS:
        status, age_note = YELLOW, f"{age_days:.1f}d old"
    else:
        status, age_note = GREEN, f"{age_days:.1f}d old"
    checks.append(
        check(
            "last_run",
            status,
            f"{len(reports)} report(s), newest {newest['url']} "
            f"at {newest['fetched_at'] or 'unknown time'} ({age_note})",
        )
    )
    if status != GREEN:
        # The budget checks above are measurements of a build this old, so do
        # not let them read as current.
        for c in checks:
            if c["name"] != "last_run" and c["status"] == GREEN:
                c["detail"] += f" — from a report {age_note}"

    overall = (
        RED
        if any(c["status"] == RED for c in checks)
        else (YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN)
    )
    return {
        "gate": "lighthouse",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": checks,
    }



def _report_age_days(report: dict) -> float | None:
    """Age of a report in days, or None if it carries no usable timestamp."""
    raw = report.get("fetched_at")
    if not raw:
        return None
    try:
        import datetime as _dt

        ts = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 86_400
    except Exception:
        return None


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".lighthouse-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--run",
        action="store_true",
        help="run `lhci autorun` first (needs a production build; slow)",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--status-path", type=Path, default=_status_path())
    args = ap.parse_args(argv)

    if args.run:
        rc, out = run_lhci()
        # lhci exits non-zero when an assertion fails, which is a result, not
        # an error — the scores are still written and parsed below.
        if rc not in (0, 1):
            print(f"lighthouse-gate: lhci failed ({rc}): {out[-500:]}", file=sys.stderr)

    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(
                f"lighthouse-gate: could not write {args.status_path}: {exc}",
                file=sys.stderr,
            )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"lighthouse gate: {report['overall'].upper()}")
        for item in report["checks"]:
            print(f"  [{icon[item['status']]}] {item['name']:<16} {item['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
