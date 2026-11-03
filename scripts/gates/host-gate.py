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
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GREEN, YELLOW, RED = "green", "yellow", "red"



def _data_home(repo_root: Path) -> Path:
    """backend/.nova, or the pre-rename backend/.deer-flow while only that exists."""
    backend = repo_root / "backend"
    new, legacy = backend / ".nova", backend / ".deer-flow"
    return legacy if not new.exists() and legacy.exists() else new

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


def _env_float(key: str, default: float) -> float:
    try:
        raw = os.environ.get(key, "").strip()
        return float(raw) if raw else default
    except ValueError:
        return default


def check_disk() -> dict:
    usage = shutil.disk_usage("/")
    pct = usage.used / usage.total * 100
    free_gb = usage.free / 1024**3
    # 85/95: at 95% the box is minutes from failing writes, and SQLite VACUUM
    # (the repair for the DB-size check below) needs headroom to run at all.
    warn = _env_float("NOVA_GATE_DISK_WARN_PCT", 85)
    fail = _env_float("NOVA_GATE_DISK_RED_PCT", 95)
    return check(
        "disk",
        _band(pct, warn, fail),
        f"{pct:.1f}% used on / ({free_gb:.0f} GiB free)",
        percent_used=round(pct, 1),
        free_bytes=usage.free,
        total_bytes=usage.total,
    )


# --------------------------------------------------------------------------- disk growth
#
# A percentage only says how full the disk is, not how fast it is filling. On
# 2026-11-02 log rotation failed every hour and left an 80 MB uncompressed copy
# behind each time; the disk check stayed green at 44 % the whole while. Two
# samples an interval apart turn that into a rate and an ETA.

DISK_HISTORY_KEEP_SEC = 3 * 86400
#: The rate is only trusted over at least this much elapsed time; shorter
#: windows are dominated by one build or one pull.
DISK_RATE_MIN_WINDOW_SEC = 3600


def _disk_history_path() -> Path:
    override = os.environ.get("NOVA_GATE_DISK_HISTORY_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "state" / "disk-history.json"


def disk_eta(samples: list[tuple[float, int]], now: float, used: int, free: int) -> tuple[float | None, float | None]:
    """(bytes/hour growth, hours until full) from the oldest sample at least
    DISK_RATE_MIN_WINDOW_SEC old; (None, None) when there is none yet. A disk
    that is shrinking or flat has no ETA."""
    usable = [(t, u) for t, u in samples if now - t >= DISK_RATE_MIN_WINDOW_SEC]
    if not usable:
        return None, None
    t0, u0 = min(usable)
    rate = (used - u0) / ((now - t0) / 3600)
    if rate <= 0:
        return rate, None
    return rate, free / rate


#: ``--print`` reads the disk history without appending to it.
_RECORD_DISK_HISTORY = True


def check_disk_growth(record: bool | None = None) -> dict:
    """How fast / is filling, and when it will be full at that rate.

    YELLOW when the ETA is under NOVA_GATE_DISK_ETA_WARN_HOURS (72), RED under
    NOVA_GATE_DISK_ETA_RED_HOURS (24). The sample history lives outside the
    gates directory (NOVA_GATE_DISK_HISTORY_PATH); ``record=False`` (``--print``)
    reads it without appending.
    """
    if record is None:
        record = _RECORD_DISK_HISTORY
    usage = shutil.disk_usage("/")
    now = time.time()
    path = _disk_history_path()
    try:
        samples = [(float(t), int(u)) for t, u in json.loads(path.read_text())]
    except (OSError, ValueError, TypeError):
        samples = []
    samples = [s for s in samples if now - s[0] <= DISK_HISTORY_KEEP_SEC]
    if record:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([*samples, (now, usage.used)][-500:]))
        except OSError:
            pass
    rate, eta = disk_eta(samples, now, usage.used, usage.free)
    if rate is None:
        return check("disk_growth", GREEN, "collecting samples (needs one at least 1 h old)", samples=len(samples))
    gb_per_day = rate * 24 / 1024**3
    if eta is None:
        return check("disk_growth", GREEN, f"/ is not growing ({gb_per_day:+.1f} GiB/day)", gib_per_day=round(gb_per_day, 2))
    warn = _env_float("NOVA_GATE_DISK_ETA_WARN_HOURS", 72)
    fail = _env_float("NOVA_GATE_DISK_ETA_RED_HOURS", 24)
    status = RED if eta < fail else (YELLOW if eta < warn else GREEN)
    detail = f"/ growing {gb_per_day:+.1f} GiB/day — full in {eta / 24:.1f} days at this rate"
    if status != GREEN:
        detail += " — find the writer (docker system df, du -sh logs/ backend/.nova)"
    return check("disk_growth", status, detail, gib_per_day=round(gb_per_day, 2), hours_to_full=round(eta, 1))


# --------------------------------------------------------------------------- docker growth

_SIZE_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5}


def parse_docker_size(text: str) -> int:
    """``"37.57GB"`` / ``"2.509kB"`` / ``"0B"`` -> bytes (docker uses SI units)."""
    raw = (text or "").strip().split(" ", 1)[0]
    number = raw.rstrip("kKMGTPB")
    unit = raw[len(number) :].upper() or "B"
    try:
        return int(round(float(number) * _SIZE_UNITS.get(unit, 1)))
    except ValueError:
        return 0


_DOCKER_DF_CACHE: dict | None = None


def _docker_df_verbose() -> tuple[dict | None, str]:
    """``docker system df -v`` as JSON, cached for the run (it is slow)."""
    global _DOCKER_DF_CACHE
    if _DOCKER_DF_CACHE is not None:
        return _DOCKER_DF_CACHE, ""
    timeout_s = float(os.environ.get("NOVA_DOCKER_DF_TIMEOUT", "90"))
    try:
        out = subprocess.run(["docker", "system", "df", "-v", "--format", "{{json .}}"], capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, f"docker system df -v exceeded {timeout_s:.0f}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"docker unavailable: {exc}"
    if out.returncode != 0:
        return None, f"docker error: {out.stderr.strip()[:120] or 'unknown'}"
    try:
        _DOCKER_DF_CACHE = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        return None, f"unparseable docker system df output: {exc}"
    return _DOCKER_DF_CACHE, ""


def evaluate_build_cache(df: dict, warn_gb: float, fail_gb: float) -> dict:
    entries = df.get("BuildCache") or []
    total = sum(parse_docker_size(e.get("Size", "")) for e in entries)
    in_use = sum(parse_docker_size(e.get("Size", "")) for e in entries if str(e.get("InUse")).lower() == "true")
    gb = total / 1000**3
    status = _band(gb, warn_gb, fail_gb)
    detail = f"{gb:.1f} GB build cache in {len(entries)} entries ({in_use / 1000**3:.1f} GB in use)"
    if status != GREEN:
        detail += f" — over {fail_gb if status == RED else warn_gb:.0f} GB; reclaim with: docker builder prune --filter until=168h"
    return check("docker_build_cache", status, detail, bytes=total, in_use_bytes=in_use, entries=len(entries), warn_gb=warn_gb, red_gb=fail_gb)


def evaluate_volumes(df: dict, warn_gb: float, fail_gb: float) -> dict:
    """Largest single volume against the thresholds; total reported too.

    A single volume is the useful unit: the Postgres data volume grows with the
    checkpoint table, the ollama volume with every model pull.
    """
    volumes = sorted(((v.get("Name", "?"), parse_docker_size(v.get("Size", ""))) for v in df.get("Volumes") or []), key=lambda nv: -nv[1])
    total = sum(size for _, size in volumes)
    if not volumes:
        return check("docker_volumes", GREEN, "no docker volumes", total_bytes=0, largest=[])
    name, size = volumes[0]
    status = _band(size / 1000**3, warn_gb, fail_gb)
    top = ", ".join(f"{n[:40]} {s / 1000**3:.1f} GB" for n, s in volumes[:4])
    detail = f"{len(volumes)} volumes, {total / 1000**3:.1f} GB total; largest: {top}"
    if status != GREEN:
        detail += f" — {name} is over {fail_gb if status == RED else warn_gb:.0f} GB"
    return check(
        "docker_volumes",
        status,
        detail,
        total_bytes=total,
        largest=[{"name": n, "bytes": s} for n, s in volumes[:10]],
        warn_gb=warn_gb,
        red_gb=fail_gb,
    )


def check_docker_build_cache() -> dict:
    df, err = _docker_df_verbose()
    if df is None:
        return check("docker_build_cache", YELLOW, err)
    return evaluate_build_cache(df, _env_float("NOVA_GATE_BUILD_CACHE_WARN_GB", 50), _env_float("NOVA_GATE_BUILD_CACHE_RED_GB", 100))


def check_docker_volumes() -> dict:
    df, err = _docker_df_verbose()
    if df is None:
        return check("docker_volumes", YELLOW, err)
    return evaluate_volumes(df, _env_float("NOVA_GATE_VOLUME_WARN_GB", 50), _env_float("NOVA_GATE_VOLUME_RED_GB", 100))


def check_inodes() -> dict:
    try:
        st = os.statvfs("/")
    except OSError as exc:
        return check("inodes", YELLOW, f"unavailable: {exc}")
    if st.f_files == 0:
        return check("inodes", GREEN, "not applicable on this filesystem")
    pct = (st.f_files - st.f_ffree) / st.f_files * 100
    return check(
        "inodes",
        _band(pct, 85, 95),
        f"{pct:.1f}% of inodes used",
        percent_used=round(pct, 1),
    )


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
    return check(
        "memory",
        _band(used_pct, 85, 95),
        f"{available / 1024**3:.1f} GiB available of {total / 1024**3:.1f} GiB",
        percent_used=round(used_pct, 1),
    )


def check_swap() -> dict:
    """Disk-swap exhaustion is the thrash signal.

    The 2026-08-20 freeze happened with swap at 6.6 GiB of 8 GiB. The box was
    never OOM-killed; it simply could not make progress. Sustained swap use is
    the earliest cheap indicator of that state.

    /proc/meminfo's SwapTotal sums *every* swap device, which since the zram
    hardening means this box's 8 GiB disk swapfile and its 8 GiB zram device are
    added together as if they were one resource. They are not comparable. zram
    is RAM-backed and compressed — 4.8 GiB of pages there occupy ~1.8 GiB of
    actual memory and cost no IO — so a full zram device is the design working,
    while a full swapfile is the box about to stall. Summing them reported
    "11.7 GiB of 16.0 GiB" for a host whose real pressure was 6.8 GiB of 8 GiB
    on disk, understating the number that matters while inventing headroom that
    does not exist.

    So: band on the disk swapfile alone, and report zram separately as context.
    """
    zram_used = 0
    disk_total = disk_used = 0
    try:
        for line in pathlib.Path("/proc/swaps").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            name, total_kb, used_kb = parts[0], int(parts[2]), int(parts[3])
            if name.startswith("/dev/zram"):
                zram_used += used_kb * 1024
            else:
                disk_total += total_kb * 1024
                disk_used += used_kb * 1024
    except OSError:
        mem = _meminfo()
        disk_total, disk_used = mem.get("SwapTotal", 0), mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)

    zram_note = f", zram {zram_used / 1024**3:.1f} GiB (compressed, RAM-backed)" if zram_used else ""
    if disk_total == 0:
        return check("swap", GREEN, f"no disk swap configured{zram_note}", zram_bytes=zram_used)
    used_pct = disk_used / disk_total * 100

    # Occupancy alone is a bad thrash signal: cold pages of idle services parked
    # in swap are swap doing its job, and this box legitimately runs a long tail
    # of them. What actually preceded the freeze was a full swapfile *while
    # pages were moving* — the box spending its time refaulting rather than
    # working. So escalate on paging rate, and treat a quiet full swapfile as
    # the warning it is rather than the emergency it is not.
    rate_kbs = _swap_page_rate()
    if used_pct < 50:
        status, note = GREEN, ""
    elif rate_kbs >= 2048:
        status, note = RED, f"  — thrashing, {rate_kbs:.0f} KB/s paging"
    else:
        status, note = YELLOW, f"  — parked, not thrashing ({rate_kbs:.0f} KB/s paging)"

    return check(
        "swap",
        status,
        f"{disk_used / 1024**3:.1f} GiB of {disk_total / 1024**3:.1f} GiB swapfile used{zram_note}{note}",
        percent_used=round(used_pct, 1),
        zram_bytes=zram_used,
        page_rate_kbs=round(rate_kbs, 1),
    )


def _swap_page_rate(window: float = 2.0) -> float:
    """Swap traffic in KB/s, sampled over a short window.

    /proc/vmstat's pswpin/pswpout are cumulative pages since boot, so a rate
    needs two samples. Two seconds is enough to separate an idle box from a
    refaulting one and is affordable in a gate that runs on a schedule.
    """
    def sample() -> int:
        v = {}
        for line in pathlib.Path("/proc/vmstat").read_text().splitlines():
            k, _, val = line.partition(" ")
            if k in ("pswpin", "pswpout"):
                v[k] = int(val)
        return v.get("pswpin", 0) + v.get("pswpout", 0)

    try:
        first = sample()
        time.sleep(window)
        return (sample() - first) * 4 / window  # pages -> KB at 4 KiB/page
    except (OSError, ValueError):
        return 0.0


def check_io_pressure() -> dict:
    """PSI 'full' means every task was stalled on IO -- the thrash fingerprint."""
    try:
        for line in Path("/proc/pressure/io").read_text().splitlines():
            if line.startswith("full"):
                avg60 = float(
                    next(p for p in line.split() if p.startswith("avg60=")).split("=")[
                        1
                    ]
                )
                return check(
                    "io_pressure",
                    _band(avg60, 10, 30),
                    f"full avg60={avg60:.2f}%",
                    avg60=avg60,
                )
    except (OSError, ValueError, StopIteration):
        pass
    return check("io_pressure", GREEN, "unavailable (no PSI)")


def _active_backend() -> tuple[str, str | None]:
    """Which engine holds the live data, per config.yaml.

    After the 2026-08-21 Postgres migration the SQLite file is a stale rollback
    copy. A size gate still pointed at it would sit green forever while the real
    database grew — the same silent-decay failure this gate exists to catch,
    reintroduced by the migration itself.
    """
    config = REPO_ROOT / "config.yaml"
    backend, url = "sqlite", None
    try:
        text = config.read_text()
    except OSError:
        return backend, None
    in_db = False
    for line in text.splitlines():
        if line.startswith("database:"):
            in_db = True
            continue
        if in_db:
            if line and not line[0].isspace():
                break
            stripped = line.strip()
            if stripped.startswith("backend:"):
                backend = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("postgres_url:"):
                url = stripped.split(":", 1)[1].strip()
    if url and url.startswith("$"):
        url = os.environ.get(url[1:], "")
    return backend, (url or os.environ.get("DATABASE_URL") or None)



def _pg_containers() -> list[str]:
    """Postgres container names to try: NOVA_PG_CONTAINER pins one; otherwise
    nova-postgres, then the pre-rename deer-flow-postgres (so the gate keeps
    working on a host that has not run scripts/migrate-deerflow-to-nova.sh)."""
    pinned = os.environ.get("NOVA_PG_CONTAINER")
    return [pinned] if pinned else ["nova-postgres", "deer-flow-postgres"]


def _no_such_container(proc) -> bool:
    return proc.returncode != 0 and "No such container" in (proc.stderr or "")

def _psql(query: str, timeout_s: float = 30) -> str | None:
    """Run one query via the postgres container.

    Uses `docker exec psql` rather than a Python driver on purpose: this gate
    runs under the system interpreter from the gates daemon, where psycopg is
    not importable. Shelling into the container keeps the gate dependency-free.
    """
    try:
        for container in _pg_containers():
            out = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-U",
                    os.environ.get("NOVA_PG_USER", "nova"),
                    "-d",
                    os.environ.get("NOVA_PG_DB", "nova"),
                    "-tAc",
                    query,
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                close_fds=False,
            )
            if not _no_such_container(out):
                break
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def check_database() -> dict:
    """The check that would have caught the incident.

    A LangGraph checkpoint table with no retention grows without bound; at
    59 GB it held the SQLite write lock long enough that concurrent writers
    timed out, surfacing as `database is locked` and HTTP 500s on thread
    creation. Bounding the file size turns that silent decay into a gate.
    """
    backend, _ = _active_backend()
    if backend == "postgres":
        raw = _psql("SELECT pg_database_size(current_database())")
        if raw is None or not raw.isdigit():
            return check(
                "database_size",
                YELLOW,
                "postgres backend, but the database is not reachable",
            )
        size = int(raw)
        gb = size / 1024**3
        warn = float(os.environ.get("NOVA_DB_WARN_GB", "5"))
        fail = float(os.environ.get("NOVA_DB_FAIL_GB", "15"))
        status = RED if gb >= fail else YELLOW if gb >= warn else GREEN
        detail = f"{gb:.2f} GB (postgres)"
        if status != GREEN:
            detail += "  — run scripts/prune-checkpoints.py"
        return check("database_size", status, detail, bytes=size, backend="postgres")

    db = _data_home(REPO_ROOT) / "data" / "deerflow.db"
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
    return check(
        "database_size", status, detail, bytes=size, warn_gb=warn, fail_gb=fail
    )


def check_checkpoint_count() -> dict:
    """How many checkpoints the pruner would delete right now.

    This measures *backlog*, not volume. An earlier version reported
    checkpoints-per-thread against a fixed threshold, which is meaningless at
    small thread counts: one agent mid-session legitimately produced 538
    checkpoints across a single thread and tripped a RED that described nothing
    wrong. Volume is supposed to be high while work is happening — retention
    keeps everything inside the recency window on purpose.

    What actually indicates a problem is rows surviving *past* that window: if
    the pruner is running, almost nothing should be older than
    NOVA_PRUNE_KEEP_DAYS beyond the newest few per thread. A growing backlog is
    the 59 GB failure returning, and it shows here days before database_size
    notices.
    """
    keep_days = float(os.environ.get("NOVA_PRUNE_KEEP_DAYS", "2"))
    keep_per_thread = int(os.environ.get("NOVA_PRUNE_KEEP_PER_THREAD", "3"))
    cutoff = _uuid6_at(time.time() - keep_days * 86400)

    backend, _ = _active_backend()
    if backend == "postgres":
        raw = _psql(
            "WITH ranked AS (SELECT checkpoint_id, ROW_NUMBER() OVER "
            "(PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn "
            "FROM checkpoints) "
            f"SELECT (SELECT COUNT(*) FROM checkpoints), COUNT(*) FROM ranked "
            f"WHERE rn > {keep_per_thread} AND checkpoint_id < '{cutoff}'"
        )
        if not raw or "|" not in raw:
            return check("checkpoint_backlog", YELLOW,
                         "postgres backend, but the database is not reachable")
        total_s, backlog_s = raw.split("|", 1)
        total, backlog = int(total_s or 0), int(backlog_s or 0)
    else:
        db = _data_home(REPO_ROOT) / "data" / "deerflow.db"
        if not db.exists():
            return check("checkpoint_backlog", GREEN, "sqlite not in use")
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                total = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
                backlog = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT checkpoint_id, ROW_NUMBER() OVER "
                    "(PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn "
                    "FROM checkpoints) WHERE rn > ? AND checkpoint_id < ?",
                    (keep_per_thread, cutoff),
                ).fetchone()[0]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - a gate must not crash the console
            return check("checkpoint_backlog", YELLOW, f"unreadable: {exc}")

    detail = f"{backlog:,} prunable of {total:,} total ({backend})"
    if backlog == 0:
        return check("checkpoint_backlog", GREEN, detail, total=total, backlog=0)
    status = _band(backlog, 500, 5000)
    if status != GREEN:
        detail += "  — is the prune job running?"
    return check("checkpoint_backlog", status, detail, total=total, backlog=backlog)


def check_workspace_backlog() -> dict:
    """Regenerable build output sitting in idle thread workspaces.

    Thread workspaces grow without bound the same way checkpoints did, but the
    fix is different because the contents are different. A workspace holds the
    agent's actual deliverables, which must never be deleted on a timer. When
    this was first measured (2026-08-21) it was 7.3 GB — and 99% of that was
    ``node_modules``, ``.next`` and ``.venv``. The deliverables were ~90 MB.

    So the number worth watching is not workspace size, it is how much
    *regenerable* weight is parked in workspaces nobody has touched — exactly
    what ``scripts/prune-workspaces.py`` would remove. Reported the same way as
    checkpoint_backlog: a growing figure means the pruner stopped running.
    """
    script = REPO_ROOT / "scripts" / "prune-workspaces.py"
    if not script.exists():
        return check("workspace_backlog", YELLOW, "prune-workspaces.py is missing")
    try:
        out = subprocess.run(
            [sys.executable, str(script), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if out.returncode != 0:
            return check("workspace_backlog", YELLOW, f"probe failed: {out.stderr.strip()[:120]}")
        data = json.loads(out.stdout)
    except Exception as exc:  # noqa: BLE001 - a gate must not crash the console
        return check("workspace_backlog", YELLOW, f"unreadable: {exc}")

    gib = data.get("removed_bytes", 0) / 1024**3
    dirs = data.get("removed_dirs", 0)
    detail = f"{gib:.2f} GB prunable in {dirs} dirs, {data.get('skipped_active_workspaces', 0)} active workspaces"
    if dirs == 0:
        return check("workspace_backlog", GREEN, "0 prunable", backlog_bytes=0)
    status = _band(gib, 5, 20)
    if status != GREEN:
        detail += "  — is the workspace prune job running?"
    return check("workspace_backlog", status, detail, backlog_bytes=data.get("removed_bytes", 0))


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
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return check(
            "docker_reclaimable",
            YELLOW,
            f"docker system df exceeded {timeout_s:.0f}s "
            "(daemon is up but slow — many images/volumes)",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return check("docker_reclaimable", YELLOW, f"docker unavailable: {exc}")
    if out.returncode != 0:
        return check(
            "docker_reclaimable",
            YELLOW,
            f"docker error: {out.stderr.strip()[:120] or 'unknown'}",
        )

    parts = [line for line in out.stdout.strip().splitlines() if line.strip()]
    return check("docker_reclaimable", GREEN, "; ".join(parts) or "nothing reported")


_GREGORIAN_TO_UNIX_TICKS = 122_192_928_000_000_000


def _uuid6_at(epoch: float) -> str:
    """Smallest UUIDv6 at a given time, for comparing against checkpoint_id.

    LangGraph mints checkpoint_id as UUIDv6, which is big-endian time-first, so
    lexical order is chronological order and a retention window can be expressed
    as a single string comparison. Deliberately duplicated from
    scripts/prune-checkpoints.py rather than imported: this gate runs under the
    system interpreter from the gates daemon, with no package on the path.
    """
    ticks = int(epoch * 10_000_000) + _GREGORIAN_TO_UNIX_TICKS
    return f"{(ticks >> 28) & 0xFFFFFFFF:08x}-{(ticks >> 12) & 0xFFFF:04x}-6{ticks & 0x0FFF:03x}-0000-000000000000"


def _jobs_state() -> dict:
    path = os.environ.get("NOVA_GATE_JOBS_PATH", "").strip()
    target = Path(path) if path else Path.home() / ".nova" / "gates" / "jobs.json"
    try:
        return json.loads(target.read_text()).get("jobs", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _job_check(name: str, label: str, max_age_sec: float, hint: str) -> dict:
    """Assert a scheduled maintenance job is still running and succeeding.

    This exists because the failure it catches is silent by construction. The
    checkpoint pruner not running looks exactly like the pruner running with
    nothing to do, right up until the database is 59 GB again. The gate has to
    go yellow when the job STOPS, not when its consequences show up -- by then
    `database_size` is already red and the outage has happened.
    """
    job = _jobs_state().get(name)
    if not job:
        return check(label, YELLOW, f"has never run — {hint}", ran=False)

    age = time.time() - (job.get("at_epoch") or 0)
    rc = job.get("exit_code")
    detail = job.get("detail", "")
    age_h = age / 3600

    if rc is None:
        return check(
            label, RED, f"last run could not complete: {detail}", age_sec=round(age)
        )
    if rc != 0:
        return check(
            label, RED, f"last run failed (exit {rc}): {detail}", age_sec=round(age)
        )
    if age > max_age_sec:
        return check(
            label,
            YELLOW,
            f"last ran {age_h:.1f}h ago (expected within "
            f"{max_age_sec / 3600:.0f}h) — {hint}",
            age_sec=round(age),
        )
    return check(
        label, GREEN, f"ran {age_h:.1f}h ago — {detail}"[:160], age_sec=round(age)
    )


def check_prune_job() -> dict:
    # Daily cadence; two missed days is a real problem, one is noise.
    return _job_check("prune", "prune_job", 2 * 86_400, "is nova-gates running?")


def check_rotate_job() -> dict:
    return _job_check("rotate", "rotate_job", 6 * 3600, "is nova-gates running?")


CHECKS = (
    check_disk,
    check_disk_growth,
    check_inodes,
    check_memory,
    check_swap,
    check_io_pressure,
    check_database,
    check_checkpoint_count,
    check_workspace_backlog,
    check_logs,
    check_docker_reclaimable,
    check_docker_build_cache,
    check_docker_volumes,
    check_prune_job,
    check_rotate_job,
)


def build_report() -> dict:
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append(
                check(getattr(fn, "__name__", "check").replace("check_", ""), YELLOW, f"check raised: {exc}")
            )
    overall = (
        RED
        if any(r["status"] == RED for r in results)
        else (YELLOW if any(r["status"] == YELLOW for r in results) else GREEN)
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
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".host-gate-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="echo the JSON document")
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print only; do not write the status file",
    )
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    args = parser.parse_args(argv)

    global _RECORD_DISK_HISTORY
    _RECORD_DISK_HISTORY = not args.print_only
    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(
                f"host-gate: could not write {args.status_path}: {exc}", file=sys.stderr
            )

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
