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


class Producer:
    def __init__(self, name: str, argv: list[str], interval_sec: float, timeout_sec: float):
        self.name = name
        self.argv = argv
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self.last_run = 0.0

    def due(self, now: float) -> bool:
        return now - self.last_run >= self.interval_sec

    def run(self) -> dict:
        started = time.time()
        script = REPO_ROOT / self.argv[0]
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *self.argv[1:]],
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
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()
            detail = tail[0][:200] if tail else ""
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

        return {"gate": self.name, "exit_code": rc, "duration_sec": round(duration, 2),
                "detail": detail}


def build_producers() -> list[Producer]:
    def env_float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, "") or default)
        except ValueError:
            return default

    return [
        Producer("host", ["scripts/gates/host-gate.py"],
                 env_float("NOVA_GATE_HOST_INTERVAL", 300), timeout_sec=180),
        Producer("drift", ["scripts/gates/drift-gate.py"],
                 env_float("NOVA_GATE_DRIFT_INTERVAL", 900), timeout_sec=180),
        Producer("ci", ["scripts/gates/ci-gate.py", "--tier", "fast"],
                 env_float("NOVA_GATE_CI_INTERVAL", 21_600), timeout_sec=1800),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true",
                    help="run every producer once and exit")
    ap.add_argument("--only", default="",
                    help="comma-separated gate names (host, drift, ci)")
    ap.add_argument("--tick", type=float, default=15.0,
                    help="seconds between due-checks")
    args = ap.parse_args(argv)

    producers = build_producers()
    if args.only:
        wanted = {w.strip() for w in args.only.split(",") if w.strip()}
        producers = [p for p in producers if p.name in wanted]
    if not producers:
        log.error("no producers selected")
        return 2

    if args.once:
        results = [p.run() for p in producers]
        print(json.dumps(results, indent=2))
        return 0

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("started; cadence: %s",
             ", ".join(f"{p.name}={p.interval_sec:.0f}s" for p in producers))

    # Run everything once at boot so a restart never leaves a stale file
    # sitting there for a full interval.
    for producer in producers:
        producer.run()

    while not stop.is_set():
        now = time.time()
        for producer in producers:
            if stop.is_set():
                break
            if producer.due(now):
                producer.run()
        stop.wait(args.tick)

    log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
