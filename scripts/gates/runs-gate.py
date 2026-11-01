#!/usr/bin/env python3
"""Run-liveness gate: is anything actually running, and did it persist?

Why this exists
---------------
Ten gate producers and sixteen watchdog probes cover the host, the
deployment, the CI, the capability registry and the services. Not one of
them looks at a *run*. A run can freeze completely silently: the ``runs``
row keeps ``status='running'`` with a stale ``updated_at``, the agent has
been gone for an hour, and every other surface of the system says green.

Concretely, on 2026-11-01 run 3cc50519 sat ``running`` with 311 LLM calls
and **zero** persisted ``run_events``. Nothing noticed: the row was
``running``, the gateway answered /health, the containers were up. The run
had died with its journal unwritten, and the only evidence was a row
nobody was reading.

So this gate answers four questions about the runs table:

- ``stuck_runs``            — a run that says it is running but has not
  moved for longer than the threshold. Progress snapshots bump
  ``updated_at`` every ~5 s while a run is alive, so a stale row is a
  dead run, not a slow one.
- ``journal_persistence``   — a run that recorded LLM calls but persisted
  NO ``run_events``. That is the exact signature of the silent-journal
  failure above.
- ``multi_instance_providers`` — more than one live ``AioSandboxProvider``
  in one gateway process. Each instance runs its own idle reaper, so two
  of them can destroy the sandbox the third is running a build in.
- ``run_completion_latency`` — the p95 of how long runs take to reach a
  terminal status. A signal, not a verdict: it is yellow when it drifts.

Output is a JSON document written atomically to ``~/.nova/gates/runs.json``
in the same envelope every other gate uses, so the console renders it with
one component and the watchdog can escalate it with one probe.

Usage:
    scripts/gates/runs-gate.py            # write the status file, print summary
    scripts/gates/runs-gate.py --json     # ... and echo the JSON
    scripts/gates/runs-gate.py --print    # print only, do not write

Exit code is 0 when every check is green or yellow, 1 when any is red, so it
can also be used directly as a CI/cron gate.

Environment overrides
---------------------
NOVA_GATE_STUCK_RUN_SECONDS        stuck-run threshold (default 1800)
NOVA_GATE_JOURNAL_WINDOW_SECONDS   how far back the journal check looks
                                   (default 3600)
NOVA_GATE_LATENCY_P95_SECONDS      p95 completion-latency threshold (3600)
NOVA_GATE_LIVE_PROVIDER_INSTANCES  explicit live AioSandboxProvider count
NOVA_GATE_GATEWAY_LOG              path to the gateway log (default
                                   <repo>/logs/gateway.log)
NOVA_PG_CONTAINER / NOVA_PG_USER / NOVA_PG_DB   postgres connection
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GREEN, YELLOW, RED = "green", "yellow", "red"

#: A run is terminal once it can no longer change its mind.
TERMINAL_STATUSES = ("success", "error", "timeout", "interrupted")

#: Default thresholds (seconds). Every one is env-overridable — a
#: deployment that legitimately runs longer raises the bound rather than
#: living with a permanently red gate.
DEFAULT_STUCK_RUN_SECONDS = 1800
DEFAULT_JOURNAL_WINDOW_SECONDS = 3600
DEFAULT_LATENCY_P95_SECONDS = 3600

#: How many rows a single check may pull back. The detail lists the
#: offenders; the count comes from a separate COUNT(*) query.
DETAIL_LIMIT = 20

#: Terminal runs sampled for the latency distribution.
LATENCY_SAMPLE = 500

#: The provider's own warning, emitted by
#: ``deerflow.community.aio_sandbox.aio_sandbox_provider._register_live_instance``
#: every time a second provider is constructed in the same process:
#:   "more than one AioSandboxProvider instance in this process (N) - ..."
PROVIDER_MULTI_WARNING = re.compile(r"more than one AioSandboxProvider instance in this process \((\d+)\)")

#: Written by docker/dev-entrypoint.sh at every gateway start.
BOOT_MARKER = "===== gateway boot "


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


def _default_status_path() -> Path:
    override = os.environ.get("NOVA_RUNS_GATE_STATUS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "gates" / "runs.json"


# ---------------------------------------------------------------------------
# database access
# ---------------------------------------------------------------------------


def _active_backend() -> tuple[str, str | None]:
    """Which engine holds the live data, per config.yaml.

    Mirrors scripts/gates/host-gate.py::_active_backend. Deliberately
    duplicated rather than imported: that file's name is not importable as
    a module, and every gate producer is a standalone script with no
    package on sys.path (they run under the system interpreter from the
    gates daemon).
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


class Dialect:
    """The handful of places SQL differs between postgres and sqlite.

    One class per engine, one place per difference: every query in this
    file is written once and parameterised by these three expressions.
    """

    name = "unknown"

    def now(self) -> str:
        raise NotImplementedError

    def seconds_ago(self, seconds: float) -> str:
        """An expression for "now minus N seconds"."""
        raise NotImplementedError

    def seconds_between(self, start: str, end: str) -> str:
        """An expression for the number of seconds between two columns."""
        raise NotImplementedError


class PostgresDialect(Dialect):
    name = "postgres"

    def now(self) -> str:
        return "now()"

    def seconds_ago(self, seconds: float) -> str:
        return f"now() - interval '{int(seconds)} seconds'"

    def seconds_between(self, start: str, end: str) -> str:
        return f"EXTRACT(EPOCH FROM ({start} - {end}))"


class SqliteDialect(Dialect):
    name = "sqlite"

    def now(self) -> str:
        return "datetime('now')"

    def seconds_ago(self, seconds: float) -> str:
        return f"datetime('now','-{int(seconds)} seconds')"

    def seconds_between(self, start: str, end: str) -> str:
        return f"(strftime('%s', {start}) - strftime('%s', {end}))"


def _dialect() -> Dialect:
    backend, _ = _active_backend()
    return PostgresDialect() if backend == "postgres" else SqliteDialect()


#: Field separator for `psql -tA -F`. A unit separator cannot occur in a
#: run id, a status or a timestamp, so a row can never be split wrong.
_FIELD_SEP = "\x1f"


def _psql(query: str, timeout_s: float = 30) -> str | None:
    """Run one query via the postgres container.

    Uses `docker exec psql` rather than a Python driver on purpose: this
    gate runs under the system interpreter from the gates daemon, where
    psycopg is not importable. Shelling into the container keeps the gate
    dependency-free. Mirrors scripts/gates/host-gate.py::_psql.
    """
    try:
        out = subprocess.run(
            [
                "docker",
                "exec",
                os.environ.get("NOVA_PG_CONTAINER", "deer-flow-postgres"),
                "psql",
                "-U",
                os.environ.get("NOVA_PG_USER", "nova"),
                "-d",
                os.environ.get("NOVA_PG_DB", "nova"),
                "-tA",
                "-F",
                _FIELD_SEP,
                "-c",
                query,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            close_fds=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _sqlite_rows(query: str, timeout_s: float = 5) -> list[list[str]] | None:
    """Run one query against the sqlite file, read-only."""
    db = REPO_ROOT / "backend" / ".deer-flow" / "data" / "deerflow.db"
    if not db.exists():
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=timeout_s)
        try:
            return [["" if v is None else str(v) for v in row] for row in conn.execute(query).fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a gate must not crash the console
        return None


def _query_rows(dialect: Dialect, query: str) -> list[list[str]] | None:
    """Rows as lists of strings, or None when the query could not run."""
    if dialect.name == "postgres":
        raw = _psql(query)
        if raw is None:
            return None
        return [line.split(_FIELD_SEP) for line in raw.splitlines() if line != ""]
    return _sqlite_rows(query)


def _query_scalar(dialect: Dialect, query: str) -> int | None:
    """First column of the first row, as an int, or None."""
    rows = _query_rows(dialect, query)
    if not rows:
        return None
    try:
        return int(str(rows[0][0]).strip())
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_stuck_runs(dialect: Dialect) -> dict:
    """A run that says it is running but has not moved for too long.

    The worker flushes a progress snapshot every ~5 s while a run is alive
    (``RunJournal._progress_flush_interval``), and every snapshot bumps
    ``runs.updated_at``. So a row whose ``updated_at`` is stale is a run
    that stopped making progress — not a run that is merely slow.
    """
    threshold = _env_float("NOVA_GATE_STUCK_RUN_SECONDS", DEFAULT_STUCK_RUN_SECONDS)
    where = f"status IN ('running','pending') AND updated_at < {dialect.seconds_ago(threshold)}"
    rows = _query_rows(
        dialect,
        f"SELECT run_id, status, updated_at FROM runs WHERE {where} ORDER BY updated_at ASC LIMIT {DETAIL_LIMIT}",
    )
    if rows is None:
        return check("stuck_runs", YELLOW, f"could not query the runs table ({dialect.name})", threshold_sec=threshold)
    if not rows:
        return check(
            "stuck_runs",
            GREEN,
            f"no run has been running/pending for more than {threshold:.0f}s",
            threshold_sec=threshold,
            stuck=0,
        )
    ids = ", ".join(row[0] for row in rows[:5])
    count = _query_scalar(dialect, f"SELECT COUNT(*) FROM runs WHERE {where}")
    total = count if count is not None else len(rows)
    return check(
        "stuck_runs",
        RED,
        f"{total} run(s) stuck in running/pending for over {threshold:.0f}s: {ids}",
        threshold_sec=threshold,
        stuck=total,
        run_ids=[row[0] for row in rows],
    )


def check_journal_persistence(dialect: Dialect) -> dict:
    """A run that made LLM calls but persisted no run_events.

    This is the signature of the silent-journal failure: the run did work
    (``llm_call_count > 0``) and the journal holds nothing for it, so the
    transcript the user is owed does not exist.

    History is not the signal — a run that lost its events to a gateway
    restart last week cannot be repaired by a gate. What matters is
    whether it is happening *now*, so the window covers two cases:

    - a run that is still active (any non-terminal status) always counts,
      because a live run with no events is losing them right now;
    - a run created inside the window counts, because it is recent enough
      that the journal should already have flushed it.
    """
    window = _env_float("NOVA_GATE_JOURNAL_WINDOW_SECONDS", DEFAULT_JOURNAL_WINDOW_SECONDS)
    terminal = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
    where = (
        "r.llm_call_count > 0 "
        "AND NOT EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = r.run_id) "
        f"AND (r.status NOT IN ({terminal}) OR r.created_at > {dialect.seconds_ago(window)})"
    )
    rows = _query_rows(
        dialect,
        f"SELECT r.run_id, r.status, r.created_at, r.llm_call_count FROM runs r "
        f"WHERE {where} ORDER BY r.created_at DESC LIMIT {DETAIL_LIMIT}",
    )
    if rows is None:
        return check("journal_persistence", YELLOW, f"could not query the runs table ({dialect.name})", window_sec=window)
    if not rows:
        return check(
            "journal_persistence",
            GREEN,
            f"every run with LLM calls persisted its events (last {window:.0f}s)",
            window_sec=window,
            missing=0,
        )
    count = _query_scalar(dialect, f"SELECT COUNT(*) FROM runs r WHERE {where}")
    total = count if count is not None else len(rows)
    ids = ", ".join(row[0] for row in rows[:5])
    return check(
        "journal_persistence",
        RED,
        f"{total} run(s) recorded LLM calls but persisted no run_events: {ids}",
        window_sec=window,
        missing=total,
        run_ids=[row[0] for row in rows],
    )


def _gateway_log_path() -> Path:
    override = os.environ.get("NOVA_GATE_GATEWAY_LOG", "").strip()
    return Path(override) if override else REPO_ROOT / "logs" / "gateway.log"


def _current_boot_log() -> str | None:
    """The gateway log section belonging to the current boot, or None.

    The log is appended across restarts, so a warning left by an earlier
    boot says nothing about the process running now. The entrypoint writes
    a dated banner at every start, which gives the boundary.
    """
    path = _gateway_log_path()
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    # Bounded read: the log rotates, and a 60 MB window is far more than
    # one boot's worth of it on any deployment.
    window = min(size, 64 * 1024 * 1024)
    try:
        with path.open("rb") as handle:
            handle.seek(size - window)
            raw = handle.read(window)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    index = text.rfind(BOOT_MARKER)
    if index == -1:
        return None
    return text[index:]


def _live_provider_instance_count() -> tuple[int | None, str]:
    """How many AioSandboxProvider instances the gateway process holds.

    The provider keeps a module-level registry
    (``aio_sandbox_provider._LIVE_INSTANCES``) and logs a warning naming
    the count every time a second instance is constructed. The gate runs
    on the host and cannot read another process's memory, so the warning
    is the observable form of that registry: if the current boot never
    logged it, exactly one provider was built.
    """
    override = os.environ.get("NOVA_GATE_LIVE_PROVIDER_INSTANCES", "").strip()
    if override:
        try:
            return int(override), "NOVA_GATE_LIVE_PROVIDER_INSTANCES"
        except ValueError:
            pass

    boot_log = _current_boot_log()
    if boot_log is None:
        return None, "gateway log unavailable"
    counts = [int(m) for m in PROVIDER_MULTI_WARNING.findall(boot_log)]
    if counts:
        return max(counts), "gateway log"
    return 1, "gateway log (no multi-instance warning since the last boot)"


def check_multi_instance_providers(dialect: Dialect) -> dict:
    """More than one live AioSandboxProvider in one gateway process.

    Each instance starts its own idle reaper with its own activity clock.
    On 2026-11-01 one gateway process held three providers and the two
    that never served a call destroyed the sandbox the third was running a
    build in — the user's work, gone, with no error anywhere.

    Reported yellow rather than red: the provider itself logs this as a
    warning, and the consequence is a race rather than a deterministic
    failure. It is loud in the gate detail, which is the point.
    """
    count, source = _live_provider_instance_count()
    if count is None:
        return check(
            "multi_instance_providers",
            YELLOW,
            "cannot read the live AioSandboxProvider count from this host "
            "(the registry is per-process; the gateway log has no current boot)",
            instances=None,
        )
    if count > 1:
        return check(
            "multi_instance_providers",
            YELLOW,
            f"{count} live AioSandboxProvider instances in the gateway process ({source}) — sandbox reaper races are possible",
            instances=count,
            source=source,
        )
    return check(
        "multi_instance_providers",
        GREEN,
        f"1 live AioSandboxProvider instance in the gateway process ({source})",
        instances=count,
        source=source,
    )


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted list.

    Matches postgres' ``percentile_cont`` so both engines answer the same
    question the same way.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (position - low)


def check_run_completion_latency(dialect: Dialect) -> dict:
    """How long runs take to reach a terminal status (created -> updated).

    A signal, not a verdict: a long tail here is usually a genuinely
    heavy task, and it is worth seeing the drift without paging for it.
    """
    threshold = _env_float("NOVA_GATE_LATENCY_P95_SECONDS", DEFAULT_LATENCY_P95_SECONDS)
    terminal = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
    duration = dialect.seconds_between("updated_at", "created_at")
    rows = _query_rows(
        dialect,
        f"SELECT {duration} FROM runs WHERE status IN ({terminal}) ORDER BY updated_at DESC LIMIT {LATENCY_SAMPLE}",
    )
    if rows is None:
        return check("run_completion_latency", YELLOW, f"could not query the runs table ({dialect.name})", threshold_sec=threshold)
    durations = []
    for row in rows:
        try:
            durations.append(float(str(row[0]).strip()))
        except (IndexError, ValueError):
            continue
    if not durations:
        return check(
            "run_completion_latency",
            GREEN,
            "no terminal runs yet — nothing to measure",
            threshold_sec=threshold,
            sample=0,
        )
    durations.sort()
    p50 = _percentile(durations, 0.5)
    p95 = _percentile(durations, 0.95)
    detail = f"p50 {p50:.0f}s, p95 {p95:.0f}s, max {durations[-1]:.0f}s over {len(durations)} terminal run(s)"
    status = YELLOW if p95 > threshold else GREEN
    if status != GREEN:
        detail += f" — over the {threshold:.0f}s p95 threshold"
    return check(
        "run_completion_latency",
        status,
        detail,
        threshold_sec=threshold,
        sample=len(durations),
        p50_sec=round(p50, 1),
        p95_sec=round(p95, 1),
        max_sec=round(durations[-1], 1),
    )


CHECKS = (
    check_stuck_runs,
    check_journal_persistence,
    check_multi_instance_providers,
    check_run_completion_latency,
)


def build_report() -> dict:
    dialect = _dialect()
    results = []
    for fn in CHECKS:
        try:
            results.append(fn(dialect))
        except Exception as exc:  # noqa: BLE001 - a gate must not crash the console
            results.append(check(fn.__name__.replace("check_", ""), YELLOW, f"check raised: {exc}"))
    overall = RED if any(r["status"] == RED for r in results) else (YELLOW if any(r["status"] == YELLOW for r in results) else GREEN)
    return {
        "gate": "runs",
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
        prefix=".runs-gate-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="echo the JSON document")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print only; do not write the status file")
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    args = parser.parse_args(argv)

    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(f"runs-gate: could not write {args.status_path}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"runs gate: {report['overall'].upper()}")
        for item in report["checks"]:
            print(f"  [{icon[item['status']]}] {item['name']:<26} {item['detail']}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
