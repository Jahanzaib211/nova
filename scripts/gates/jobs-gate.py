#!/usr/bin/env python3
"""Job runner gate: writes ~/.nova/gates/jobrunner.json from the gateway's
admin jobs summary (workers, queue depth, dead-letter).

Distinct from jobs.json, which records the gates-daemon's own maintenance
producers (prune, rotate) and which the ops console keys on. This one is
about the application's job runner (P2): the deer-flow-jobs worker
container behind /api/jobs.

    python3 scripts/gates/jobs-gate.py            # write + summary
    python3 scripts/gates/jobs-gate.py --json     # also echo the document
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GREEN, YELLOW, RED = "green", "yellow", "red"
WORKER_MAX_AGE_S = float(os.environ.get("NOVA_JOBS_WORKER_MAX_AGE_S", "90"))
QUEUE_WARN = int(os.environ.get("NOVA_JOBS_QUEUE_WARN", "50"))
OLDEST_QUEUED_WARN_S = float(os.environ.get("NOVA_JOBS_OLDEST_WARN_S", "600"))


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def ops_token() -> str:
    token = os.environ.get("NOVA_OPS_TOKEN", "").strip()
    if token:
        return token
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("NOVA_OPS_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def fetch_summary(base: str, token: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(
        f"{base}/api/v1/admin/jobs/summary", headers={"X-Nova-Ops-Token": token}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - fixed local URL
        return json.loads(resp.read().decode("utf-8"))


def evaluate(summary: dict) -> list[dict]:
    """Pure: turn the summary into gate checks."""
    live = [
        w
        for w in summary.get("workers", [])
        if float(w.get("last_seen_age_s", 1e9)) <= WORKER_MAX_AGE_S
    ]
    stale = len(summary.get("workers", [])) - len(live)
    checks = [
        check(
            "workers",
            GREEN if live else RED,
            f"{len(live)} live worker(s), {stale} stale"
            if live
            else f"no worker has heartbeated in {WORKER_MAX_AGE_S:.0f}s",
            live=len(live),
        )
    ]
    queued = int(summary.get("queued", 0))
    oldest = summary.get("oldest_queued_age_s")
    backlog_status = GREEN
    if queued >= QUEUE_WARN or (oldest is not None and oldest >= OLDEST_QUEUED_WARN_S):
        backlog_status = YELLOW
    checks.append(
        check(
            "backlog",
            backlog_status,
            f"{queued} queued, {int(summary.get('running', 0))} running, "
            f"{int(summary.get('retrying', 0))} retrying"
            + (f", oldest waiting {oldest:.0f}s" if oldest is not None else ""),
            queued=queued,
        )
    )
    dead = int(summary.get("dead_letter", 0))
    checks.append(
        check(
            "dead_letter",
            GREEN if dead == 0 else YELLOW,
            f"{dead} dead-lettered job(s)"
            + (" — retry or delete them from the console" if dead else ""),
            dead_letter=dead,
        )
    )
    return checks


def build_report(summary: dict | None, error: str | None = None) -> dict:
    checks = (
        evaluate(summary)
        if summary is not None
        else [check("summary", RED, f"summary unavailable: {error}")]
    )
    overall = (
        RED
        if any(c["status"] == RED for c in checks)
        else (YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN)
    )
    return {
        "gate": "jobrunner",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": checks,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".jobs-gate-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def _default_status_path() -> Path:
    override = os.environ.get("NOVA_JOBRUNNER_GATE_STATUS_PATH", "").strip()
    return (
        Path(override)
        if override
        else Path.home() / ".nova" / "gates" / "jobrunner.json"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print", dest="print_only", action="store_true")
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    parser.add_argument(
        "--base", default=os.environ.get("NOVA_BASE_URL", "http://127.0.0.1:2026")
    )
    args = parser.parse_args(argv)

    token = ops_token()
    summary: dict | None = None
    error: str | None = None
    if not token:
        error = "NOVA_OPS_TOKEN not set (env or .env)"
    else:
        try:
            summary = fetch_summary(args.base, token)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = str(exc)
    report = build_report(summary, error)
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(
                f"jobs-gate: could not write {args.status_path}: {exc}", file=sys.stderr
            )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"jobrunner gate: {report['overall'].upper()}")
        for item in report["checks"]:
            print(f"  [{icon[item['status']]}] {item['name']:<12} {item['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
