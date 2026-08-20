#!/usr/bin/env python3
"""Host resource gate for the Nova Ops console.

Why this exists
---------------
On 2026-08-19 Nova degraded for a day with no operator-visible cause. The
actual state of the box was: a 59.4 GB SQLite database (57.3 GB of it
unbounded LangGraph checkpoint blobs) on a 97%-full disk, with swap nearly
exhausted. Every one of those numbers was trivially observable and none of
them was on any dashboard, so the failure presented as "threads randomly stop
working" instead of "you are out of disk".

Each check below corresponds to something that actually went wrong, so the
thresholds are set where intervention is still cheap rather than where the
system is already failing.

Output is a JSON document written atomically to ~/.nova/gates/host.json in
the same shape as the healthcheck watchdog's status file, so the console can
render both with one component and age both with one staleness rule.

Usage:
    scripts/gates/host-gate.py            # write the status file, print summary
    scripts/gates/host-gate.py --json     # ... and echo the JSON
    scripts/gates/host-gate.py --print    # print only, do not write

Exit code is 0 when every check is green or yellow, 1 when any is red, so it
can also be used directly as a CI/cron gate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GREEN, YELLOW, RED = "green", "yellow", "red"


def _default_status_path() -> Path:
    override = os.environ.get("NOVA_HOST_GATE_STATUS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "gates" / "host.json"


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def _band(value: float, warn: float, fail: float) -> str:
    """GREEN below `warn`, YELLOW below `fail`, RED at or above `fail`."""
    if value >= fail:
        return RED
    if value >= warn:
        return YELLOW
    return GREEN


def check_disk() -> dict:
    usage = shutil.disk_usage("/")
    pct = usage.used / usage.total * 100
    free_gb = usage.free / 1024**3
    # 85/95: at 95% the box is minutes from failing writes, and SQLite VACUUM
    # (the repair for the DB-size check below) needs headroom to run at all.
    return check(
        "disk", _band(pct, 85, 95),
        f"{pct:.1f}% used on / ({free_gb:.0f} GiB free)",
        percent_used=round(pct, 1), free_bytes=usage.free, total_bytes=usage.total,
    )


def check_inodes() -> dict:
    try:
        st = os.statvfs("/")
    except OSError as exc:
        return check("inodes", YELLOW, f"unavailable: {exc}")
    if st.f_files == 0:
        return check("inodes", GREEN, "not applicable on this filesystem")
    pct = (st.f_files - st.f_ffree) / st.f_files * 100
    return check("inodes", _band(pct, 85, 95), f"{pct:.1f}% of inodes used",
                 percent_used=round(pct, 1))


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            values[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return values


def check_memory() -> dict:
    mem = _meminfo()
    total, available = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    if not total:
        return check("memory", YELLOW, "unavailable")
    used_pct = (total - available) / total * 100
    return check("memory", _band(used_pct, 85, 95),
                 f"{available / 1024**3:.1f} GiB available of {total / 1024**3:.1f} GiB",
                 percent_used=round(used_pct, 1))


def check_swap() -> dict:
    """Swap exhaustion is the thrash signal.

    The 2026-08-20 freeze happened with swap at 6.6 GiB of 8 GiB. The box was
    never OOM-killed; it simply could not make progress. Sustained swap use is
    the earliest cheap indicator of that state.
    """
    mem = _meminfo()
    total, free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    if total == 0:
        return check("swap", GREEN, "no swap configured")
    used_pct = (total - free) / total * 100
    return check("swap", _band(used_pct, 50, 80),
                 f"{(total - free) / 1024**3:.1f} GiB of {total / 1024**3:.1f} GiB used",
                 percent_used=round(used_pct, 1))


def check_io_pressure() -> dict:
    """PSI 'full' means every task was stalled on IO -- the thrash fingerprint."""
    try:
        for line in Path("/proc/pressure/io").read_text().splitlines():
            if line.startswith("full"):
                avg60 = float(
                    next(p for p in line.split() if p.startswith("avg60=")).split("=")[1]
                )
                return check("io_pressure", _band(avg60, 10, 30),
                             f"full avg60={avg60:.2f}%", avg60=avg60)
    except (OSError, ValueError, StopIteration):
        pass
    return check("io_pressure", GREEN, "unavailable (no PSI)")


def check_database() -> dict:
    """The check that would have caught the incident.

    A LangGraph checkpoint table with no retention grows without bound; at
    59 GB it held the SQLite write lock long enough that concurrent writers
    timed out, surfacing as `database is locked` and HTTP 500s on thread
    creation. Bounding the file size turns that silent decay into a gate.
    """
    db = REPO_ROOT / "backend" / ".deer-flow" / "data" / "deerflow.db"
    if not db.exists():
        return check("database_size", GREEN, "sqlite not in use (postgres backend?)")
    size = db.stat().st_size
    gb = size / 1024**3
    warn = float(os.environ.get("NOVA_DB_WARN_GB", "5"))
    fail = float(os.environ.get("NOVA_DB_FAIL_GB", "15"))
    status = RED if gb >= fail else YELLOW if gb >= warn else GREEN
    detail = f"{gb:.2f} GB"
    if status != GREEN:
        detail += "  — run scripts/prune-checkpoints.py"
    return check("database_size", status, detail, bytes=size,
                 warn_gb=warn, fail_gb=fail)


def check_checkpoint_count() -> dict:
    """Row count is the leading indicator; file size is the lagging one."""
    db = REPO_ROOT / "backend" / ".deer-flow" / "data" / "deerflow.db"
    if not db.exists():
        return check("checkpoint_rows", GREEN, "sqlite not in use")
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
            threads = conn.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - a gate must never crash the console
        return check("checkpoint_rows", YELLOW, f"unreadable: {exc}")

    per_thread = rows / threads if threads else 0
    # Retention keeps 3 per thread plus a 2-day window; a sustained average
    # above ~50 means the pruner is not running.
    status = _band(per_thread, 50, 200)
    return check("checkpoint_rows", status,
                 f"{rows:,} checkpoints across {threads:,} threads "
                 f"({per_thread:.0f}/thread)",
                 rows=rows, threads=threads, per_thread=round(per_thread, 1))


def check_logs() -> dict:
    """Unbounded logs are the other way this disk fills."""
    logs = REPO_ROOT / "logs"
    if not logs.is_dir():
        return check("log_size", GREEN, "no logs directory")
    total = 0
    biggest_name, biggest = "", 0
    for path in logs.rglob("*"):
        if path.is_file():
            size = path.stat().st_size
            total += size
            if size > biggest:
                biggest_name, biggest = path.name, size
    mb = total / 1024**2
    status = _band(mb, 512, 2048)
    detail = f"{mb:.0f} MB total"
    if biggest_name:
        detail += f", largest {biggest_name} at {biggest / 1024**2:.0f} MB"
    if status != GREEN:
        detail += "  — run scripts/rotate-logs.sh"
    return check("log_size", status, detail, bytes=total)


def check_docker_reclaimable() -> dict:
    # `docker system df` walks every image, container and volume; on a host
    # with a few hundred of them it routinely takes 30-60s. Distinguish that
    # from a genuinely absent daemon, because reporting a slow-but-healthy
    # Docker as "not reachable" sends the operator hunting the wrong fault.
    timeout_s = float(os.environ.get("NOVA_DOCKER_DF_TIMEOUT", "90"))
    try:
        out = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Reclaimable}}"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return check("docker_reclaimable", YELLOW,
                     f"docker system df exceeded {timeout_s:.0f}s "
                     "(daemon is up but slow — many images/volumes)")
    except (OSError, subprocess.SubprocessError) as exc:
        return check("docker_reclaimable", YELLOW, f"docker unavailable: {exc}")
    if out.returncode != 0:
        return check("docker_reclaimable", YELLOW,
                     f"docker error: {out.stderr.strip()[:120] or 'unknown'}")

    parts = [line for line in out.stdout.strip().splitlines() if line.strip()]
    return check("docker_reclaimable", GREEN, "; ".join(parts) or "nothing reported")


CHECKS = (
    check_disk, check_inodes, check_memory, check_swap, check_io_pressure,
    check_database, check_checkpoint_count, check_logs, check_docker_reclaimable,
)


def build_report() -> dict:
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append(check(fn.__name__.replace("check_", ""), YELLOW,
                                 f"check raised: {exc}"))
    overall = RED if any(r["status"] == RED for r in results) else (
        YELLOW if any(r["status"] == YELLOW for r in results) else GREEN
    )
    return {
        "gate": "host",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": results,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=".host-gate-", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="echo the JSON document")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print only; do not write the status file")
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    args = parser.parse_args(argv)

    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(f"host-gate: could not write {args.status_path}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"host gate: {report['overall'].upper()}")
        for item in report["checks"]:
            print(f"  [{icon[item['status']]}] {item['name']:<20} {item['detail']}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
