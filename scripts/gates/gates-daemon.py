#!/usr/bin/env python3
"""Keep the Nova Ops gate status files fresh.

Why this exists
---------------
The gate producers each write a JSON status file that the Nova Ops console
reads. Nothing ran them on a schedule, so the console showed
"Stale — produced 10h ago" on the host and drift gates: real numbers, silently
out of date, which is worse than no numbers at all because they still look
authoritative.

`scripts/healthcheck-daemon.py` already had a supervisor (the `nova-healthcheck`
PM2 app) and was correspondingly fresh, which is exactly the pattern this
copies for the remaining gates.

Cadence is per-gate, chosen so each file stays comfortably inside the staleness
window the console applies (3x the declared interval in nova-ops/src/lib/gates.ts):

  host   every  5 min  — cheap, and the numbers it watches move quickly
  drift  every 15 min  — only changes on deploy/commit
  ci     every  6 h    — the fast tier is ~35s of real CPU; more often than
                         this is noise, and the console has a Run button for
                         when you actually want it now

Each producer runs in its own subprocess with a hard timeout, and a producer
that fails or hangs never stops the others or the loop: a monitoring daemon
that dies with the thing it monitors is not monitoring.

Run under PM2 (see ecosystem.config.js, app `nova-gates`):
    pm2 start ecosystem.config.js --only nova-gates

Or one-shot, which is what the console's Run buttons effectively do:
    scripts/gates/gates-daemon.py --once
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nova-gates %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("nova-gates")


def _summarise(output: str) -> str:
    """One readable line from a producer's stdout.

    Several of these scripts emit indented JSON, whose first line is a bare
    "{" — which is what jobs.json recorded before this existed. Pull the
    fields that actually say what happened, and fall back to the first line
    of plain text.
    """
    text = output.strip()
    if not text:
        return ""
    if text.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text.splitlines()[0][:200]
        if isinstance(data, dict):
            if "result" in data:  # prune-checkpoints.py
                deleted = data.get("deleted") or {}
                bits = [str(data["result"])]
                if deleted:
                    bits.append(
                        f"{deleted.get('checkpoints', 0)} checkpoints, "
                        f"{deleted.get('writes', 0)} writes deleted"
                    )
                if "after" in data:
                    gb = (data["after"].get("db_bytes") or 0) / 1024**3
                    bits.append(f"db {gb:.2f} GB")
                return " — ".join(bits)[:200]
            if "logs" in data:  # rotate-logs.sh
                actions = [f"{l.get('log')}:{l.get('action')}" for l in data["logs"]]
                return ", ".join(actions)[:200]
            for key in ("overall", "detail", "status"):
                if key in data:
                    return str(data[key])[:200]
        return text.splitlines()[0][:200]
    return text.splitlines()[0][:200]


class Producer:
    """One scheduled script.

    `kind` separates the two things this daemon runs:

    - ``gate``  — writes its own ~/.nova/gates/<name>.json; the console reads
                  that file directly and this daemon only has to invoke it.
    - ``job``   — maintenance that produces no gate file of its own (the
                  checkpoint pruner, log rotation). Their outcome is recorded
                  in jobs.json so host-gate can assert they are still running;
                  an unrun pruner is exactly how the 59 GB database came back.
    """

    def __init__(
        self,
        name: str,
        argv: list[str],
        interval_sec: float,
        timeout_sec: float,
        kind: str = "gate",
    ):
        self.name = name
        self.argv = argv
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self.kind = kind
        self.last_run = 0.0

    def due(self, now: float) -> bool:
        return now - self.last_run >= self.interval_sec

    def run(self) -> dict:
        started = time.time()
        script = REPO_ROOT / self.argv[0]
        # Not everything scheduled here is Python — rotate-logs.sh is bash.
        #
        # Prefer the backend venv's interpreter when it exists. Since the
        # Postgres migration the pruner needs psycopg, which is installed there
        # and not in the system interpreter PM2 launches this daemon with —
        # without this the scheduled prune fails on Postgres and the checkpoint
        # table silently regrows, which is the whole thing this daemon exists
        # to prevent. The scripts stay importable under either interpreter, so
        # falling back is safe.
        venv_python = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
        interpreter = str(venv_python) if venv_python.exists() else sys.executable
        launcher = [interpreter] if script.suffix == ".py" else ["bash"]
        try:
            proc = subprocess.run(
                [*launcher, str(script), *self.argv[1:]],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                # close_fds=False must be set at EVERY level of the spawn chain,
                # not just the one that launches node. Measured under PM2 with
                # the identical `pnpm exec tsc --noEmit` at the leaf:
                #
                #   bash -> py(close_fds=False) -> node                 exit 0
                #   bash -> py(default)  -> py(close_fds=False) -> node exit -6
                #   bash -> py(close_fds=False) x2 -> node              exit 0
                #
                # A close_fds=True anywhere above it is enough to make the
                # grandchild abort at teardown, after it has already produced
                # correct output. See the fuller note in ci-gate.py.
                close_fds=False,
            )
            rc: int | None = proc.returncode
            detail = _summarise(proc.stdout or proc.stderr or "")
        except subprocess.TimeoutExpired:
            rc, detail = None, f"timed out after {self.timeout_sec:.0f}s"
        except (OSError, subprocess.SubprocessError) as exc:
            rc, detail = None, f"could not run: {exc}"

        self.last_run = time.time()
        duration = self.last_run - started

        # A non-zero exit means the gate found a problem, which is the gate
        # doing its job -- log it at info, not error. Only an inability to run
        # the producer at all is a fault of this daemon.
        if rc is None:
            log.error("%s: %s", self.name, detail)
        else:
            log.info("%s: exit=%s in %.1fs — %s", self.name, rc, duration, detail)

        return {
            "gate": self.name,
            "kind": self.kind,
            "exit_code": rc,
            "at_epoch": self.last_run,
            "duration_sec": round(duration, 2),
            "detail": detail,
        }


def build_producers() -> list[Producer]:
    def env_float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, "") or default)
        except ValueError:
            return default

    return [
        Producer(
            "host",
            ["scripts/gates/host-gate.py"],
            env_float("NOVA_GATE_HOST_INTERVAL", 300),
            timeout_sec=180,
        ),
        Producer(
            "drift",
            ["scripts/gates/drift-gate.py"],
            env_float("NOVA_GATE_DRIFT_INTERVAL", 900),
            timeout_sec=180,
        ),
        Producer(
            "ci",
            ["scripts/gates/ci-gate.py", "--tier", "fast"],
            env_float("NOVA_GATE_CI_INTERVAL", 21_600),
            timeout_sec=1800,
        ),
        # --- maintenance jobs -------------------------------------------------
        # The checkpoint pruner. This is the one job whose absence recreates the
        # original outage: LangGraph checkpoints grow without bound and took
        # deerflow.db to 59.4 GB. Daily is ample -- the table only has to stay
        # bounded, not minimal.
        #
        # Deliberately NO --vacuum here. VACUUM takes an exclusive lock and
        # rewrites the whole file; that belongs in a maintenance window, not in
        # a background job that could collide with a live run. Pruning keeps the
        # row count flat, which is what stops the lock contention; reclaiming
        # file bytes is a separate, manual concern.
        Producer(
            "prune",
            [
                "scripts/prune-checkpoints.py",
                "--keep-per-thread",
                "3",
                "--keep-days",
                "2",
                "--strategy",
                "rebuild",
                "--json",
            ],
            env_float("NOVA_GATE_PRUNE_INTERVAL", 86_400),
            timeout_sec=3600,
            kind="job",
        ),
        # Log rotation. gateway.log now appends rather than truncating on every
        # restart, so something has to bound it. Size-triggered, so an hourly
        # run is a no-op until it matters.
        Producer(
            "rotate",
            ["scripts/rotate-logs.sh", "--json"],
            env_float("NOVA_GATE_ROTATE_INTERVAL", 3600),
            timeout_sec=300,
            kind="job",
        ),
    ]


def jobs_path() -> Path:
    override = os.environ.get("NOVA_GATE_JOBS_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".nova" / "gates" / "jobs.json"


def record_job(result: dict) -> None:
    """Merge one job result into jobs.json. Never fatal.

    Gates publish their own status file; maintenance jobs do not, so without
    this their last run is only visible in a PM2 log nobody reads -- the same
    blind spot that let the checkpoint table grow unnoticed.
    """
    if result.get("kind") != "job":
        return
    path = jobs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except json.JSONDecodeError:
                existing = {}
        jobs = existing.get("jobs", {})
        jobs[result["gate"]] = {
            "at_epoch": result.get("at_epoch"),
            "exit_code": result.get("exit_code"),
            "duration_sec": result.get("duration_sec"),
            "detail": result.get("detail", "")[:500],
        }
        # Every other gate file carries overall/ok, and the console keys its
        # rendering and its staleness rule off them. jobs.json shipped without
        # either, so it rendered as an unknown -- a maintenance job failing
        # looked exactly like one that had never run. Derive both from the
        # recorded exit codes so this file answers the same question the
        # others do.
        failed = [n for n, j in jobs.items() if j.get("exit_code") not in (0, None)]
        overall = "red" if failed else "green"
        payload = {
            "checked_at_epoch": time.time(),
            "overall": overall,
            "ok": not failed,
            "failed_jobs": failed,
            "jobs": jobs,
        }
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".jobs-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            tmp = Path(handle.name)
        tmp.replace(path)
    except OSError:
        log.warning("could not write %s", path, exc_info=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--once", action="store_true", help="run every producer once and exit"
    )
    ap.add_argument(
        "--only", default="", help="comma-separated gate names (host, drift, ci)"
    )
    ap.add_argument(
        "--tick", type=float, default=15.0, help="seconds between due-checks"
    )
    args = ap.parse_args(argv)

    producers = build_producers()
    if args.only:
        wanted = {w.strip() for w in args.only.split(",") if w.strip()}
        producers = [p for p in producers if p.name in wanted]
    if not producers:
        log.error("no producers selected")
        return 2

    if args.once:
        results = []
        for producer in producers:
            result = producer.run()
            record_job(result)
            results.append(result)
        print(json.dumps(results, indent=2))
        return 0

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info(
        "started; cadence: %s",
        ", ".join(f"{p.name}={p.interval_sec:.0f}s" for p in producers),
    )

    # Run everything once at boot so a restart never leaves a stale file
    # sitting there for a full interval.
    for producer in producers:
        record_job(producer.run())

    while not stop.is_set():
        now = time.time()
        for producer in producers:
            if stop.is_set():
                break
            if producer.due(now):
                record_job(producer.run())
        stop.wait(args.tick)

    log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
