#!/usr/bin/env python3
"""nova-healthcheck — supervisor watchdog for the local dev stack.

Polls 9 probes every CYCLE_INTERVAL seconds and auto-fixes common breakage
so `pm2 list nova-healthcheck` is the operator's single pane of glass.

Generic by design — this code knows nothing about specific upstream services.
All probe targets are configured via environment variables.

Probes:
  P1. nginx on http://localhost:2026/health
  P2. gateway reachable via nginx (probes /api/models without creds; the
      auth wall — 401/403 — proves the gateway + auth middleware are up)
  P3. frontend root on http://localhost:2026/ (200 + html body)
  P4. local LLM gateway on http://localhost:${LOCAL_LLM_GATEWAY_PORT:-9000}/health
  P5. llama-server loopback reachability (127.0.0.1:8081/v1/models → 200)
  P6. llama-server VRAM readiness (tiny 1-token chat completion)
  P7. Docker container count for the deer-flow-dev project (≥ 3 expected)
  P8. binary attestation drift (env-driven paths; skipped if unset)
  P9. llama-bridge reachability (172.17.0.1:8081 → the path Nova's
      container actually uses via host.docker.internal)

Auto-fixes (only safe, reversible ones):
  - Attestation drift → clear the hash dir (and run WATCHDOG_ATTESTATION_RESTART_CMD
    if set)
  - Container count < 3 → pm2 restart deerflow
  - llama-server ECONNREFUSED at boot → wait one cycle, no action (cold load)

Status:
  - Exits 0 every cycle when all probes are GREEN.
  - Exits 1 if a cycle exceeds CYCLE_DEADLINE_SEC (a probe hung); PM2 will
    restart, and the next cycle starts fresh.

Reads:
  - ${LOCAL_LLM_GATEWAY_HOST:-127.0.0.1}:${LOCAL_LLM_GATEWAY_PORT:-9000} for P4
  - ${LLAMA_HOST:-127.0.0.1}:8081 for P5/P6
  - ${LLAMA_BRIDGE_HOST:-172.17.0.1}:8081 for P9
  - ${WATCHDOG_ATTESTATION_BINARY_PATH} for P8 (skipped if unset)
  - ${HEALTHCHECK_CYCLE_DEADLINE_SEC:-60} hard deadline per cycle
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

import httpx

CYCLE_DEADLINE_SEC = float(os.environ.get("HEALTHCHECK_CYCLE_DEADLINE_SEC", "60"))

# Logs go to stderr so they don't get mixed with the per-cycle JSON status
# line on stdout. PM2 captures stderr into error_file, stdout into out_file;
# `pm2 logs nova-healthcheck --lines 1 --nostream` then shows just the JSON,
# while `pm2 logs nova-healthcheck --lines 30 --nostream --err` shows the
# diagnostic narrative.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nova-healthcheck %(levelname)s %(message)s",
    stream=sys.stderr,
)
# Silence httpx's per-request INFO noise — with 8 probes per cycle and a 30s
# interval that's ~16 lines per minute of pure noise polluting the log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("nova-healthcheck")


# ---------------------------------------------------------------------------
# Status + result types
# ---------------------------------------------------------------------------


class Status(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"  # masked known-cold (e.g. VRAM still loading)
    RED = "red"  # failed; auto-fix attempted


@dataclass
class ProbeResult:
    name: str
    status: Status
    detail: str = ""
    latency_ms: float = 0.0
    fixed: bool = False  # True if auto-fix was applied this cycle

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CycleReport:
    cycle_id: int
    started_at: float
    duration_ms: float
    probes: list[ProbeResult] = field(default_factory=list)
    overall: Status = Status.GREEN
    exit_code: int = 0

    def add(self, result: ProbeResult) -> None:
        self.probes.append(result)
        if result.status == Status.RED:
            self.overall = Status.RED
            self.exit_code = 1
        elif result.status == Status.YELLOW and self.overall == Status.GREEN:
            self.overall = Status.YELLOW

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 1),
            "overall": self.overall.value,
            "exit_code": self.exit_code,
            "probes": [p.to_dict() for p in self.probes],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def timed(coro_factory: Callable[[], Awaitable[ProbeResult]]) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        return await coro_factory()
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # stamp latency on whatever the coro produced
        # (caller is responsible for putting it on the result)


def _http_probe(name: str, url: str, *, expected_status: tuple[int, ...] = (200,), timeout: float = 3.0, body_validator: Optional[Callable[[dict], bool]] = None) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.get(url)
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code not in expected_status:
                return ProbeResult(name, Status.RED, f"HTTP {res.status_code}", latency)
            if body_validator is not None:
                try:
                    payload = res.json()
                except Exception:
                    return ProbeResult(name, Status.RED, "non-JSON body", latency)
                if not body_validator(payload):
                    return ProbeResult(name, Status.RED, "body validator failed", latency)
            return ProbeResult(name, Status.GREEN, f"HTTP {res.status_code}", latency)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RequestError) as e:
            return ProbeResult(name, Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)
        except Exception as e:
            return ProbeResult(name, Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)

    return asyncio.ensure_future(_do())


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


async def probe_nginx(port: int = 2026) -> ProbeResult:
    return await _http_probe(
        "P1_nginx",
        f"http://localhost:{port}/health",
        body_validator=lambda d: d.get("status") == "healthy",
    )


async def probe_gateway(port: int = 2026) -> ProbeResult:
    # The gateway's /api/health may require auth in production; hitting
    # /api/models without creds and expecting 401 (not 5xx) proves the
    # gateway is up and the auth middleware is wired.
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"http://localhost:{port}/api/models")
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code in (401, 403):
                # Auth wall = gateway up, auth layer healthy
                return ProbeResult("P2_gateway", Status.GREEN, f"auth-wall HTTP {res.status_code}", latency)
            if res.status_code == 200:
                return ProbeResult("P2_gateway", Status.GREEN, "HTTP 200", latency)
            return ProbeResult("P2_gateway", Status.RED, f"unexpected HTTP {res.status_code}", latency)
        except Exception as e:
            return ProbeResult("P2_gateway", Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)

    return await _do()


async def probe_frontend(port: int = 2026) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"http://localhost:{port}/")
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code == 200 and "html" in res.headers.get("content-type", "").lower():
                return ProbeResult("P3_frontend", Status.GREEN, f"HTTP 200 html={len(res.text)}b", latency)
            return ProbeResult("P3_frontend", Status.RED, f"HTTP {res.status_code} ct={res.headers.get('content-type','?')}", latency)
        except Exception as e:
            return ProbeResult("P3_frontend", Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)

    return await _do()


async def probe_local_llm_gateway(port: int = 9000) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"http://localhost:{port}/health")
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code != 200:
                return ProbeResult("P4_local_llm_gateway", Status.RED, f"HTTP {res.status_code}", latency)
            payload = res.json()
            backend = payload.get("backend", "?")
            if backend == "healthy":
                return ProbeResult("P4_local_llm_gateway", Status.GREEN, f"backend={backend}", latency)
            if backend == "unreachable":
                # llama-server cold or down — mask as yellow for 2 cycles
                return ProbeResult("P4_local_llm_gateway", Status.YELLOW, f"backend={backend} (llama cold?)", latency)
            return ProbeResult("P4_local_llm_gateway", Status.RED, f"backend={backend}", latency)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return ProbeResult("P4_local_llm_gateway", Status.RED, "ECONNREFUSED", (time.perf_counter() - t0) * 1000)
        except Exception as e:
            return ProbeResult("P4_local_llm_gateway", Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)

    return await _do()


async def probe_llama_loopback(host: str = "127.0.0.1", port: int = 8081) -> ProbeResult:
    return await _http_probe(
        "P5_llama_loopback",
        f"http://{host}:{port}/v1/models",
        body_validator=lambda d: isinstance(d.get("data"), list) and len(d["data"]) >= 1,
    )


async def probe_llama_bridge(host: str = "172.17.0.1", port: int = 8081) -> ProbeResult:
    """Probe llama-server via the docker bridge IP — the path Nova's container
    actually uses (via `host.docker.internal:8081` → bridge → loopback).

    Without this probe the watchdog only sees P5_llama_loopback which targets
    127.0.0.1 directly. If the bridge dies but llama-server stays up, P5 is
    GREEN and Nova's container silently loses reachability. This probe
    catches that failure mode.
    """
    return await _http_probe(
        "P9_bridge",
        f"http://{host}:{port}/v1/models",
        body_validator=lambda d: isinstance(d.get("data"), list) and len(d["data"]) >= 1,
    )


async def probe_llama_vram(host: str = "127.0.0.1", port: int = 8081, model: str = "") -> ProbeResult:
    """Issue a 1-token chat completion to confirm VRAM is loaded.

    During the ~30s cold-load window after a restart, llama-server answers
    /v1/models but 503s on /v1/chat/completions. This probe distinguishes
    the two states.
    """
    if not model:
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                res = await client.get(f"http://{host}:{port}/v1/models")
                models = res.json().get("data", [])
                if not models:
                    return ProbeResult("P6_llama_vram", Status.YELLOW, "no model advertised")
                model = models[0]["id"]
            except Exception as e:
                return ProbeResult("P6_llama_vram", Status.RED, f"models probe failed: {e}")

    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"http://{host}:{port}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ok"}],
                        "max_tokens": 1,
                    },
                )
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code == 200:
                return ProbeResult("P6_llama_vram", Status.GREEN, f"completion OK model={model}", latency)
            if res.status_code in (503, 502, 500):
                return ProbeResult("P6_llama_vram", Status.YELLOW, f"VRAM cold HTTP {res.status_code}", latency)
            return ProbeResult("P6_llama_vram", Status.RED, f"HTTP {res.status_code}", latency)
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            return ProbeResult("P6_llama_vram", Status.YELLOW, "timeout (VRAM cold?)", (time.perf_counter() - t0) * 1000)
        except Exception as e:
            return ProbeResult("P6_llama_vram", Status.RED, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)

    return await _do()


async def probe_containers(project: str = "deer-flow-dev", min_count: int = 3) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except FileNotFoundError:
            return ProbeResult("P7_containers", Status.RED, "docker binary not found", (time.perf_counter() - t0) * 1000)
        except asyncio.TimeoutError:
            return ProbeResult("P7_containers", Status.RED, "docker ps timeout", (time.perf_counter() - t0) * 1000)
        latency = (time.perf_counter() - t0) * 1000
        if proc.returncode != 0:
            return ProbeResult("P7_containers", Status.RED, f"docker exit {proc.returncode}: {stderr.decode()[:80]}", latency)
        names = [n for n in stdout.decode().splitlines() if n.strip()]
        if len(names) < min_count:
            return ProbeResult("P7_containers", Status.RED, f"only {len(names)} containers (need ≥{min_count}): {names}", latency)
        return ProbeResult("P7_containers", Status.GREEN, f"{len(names)} containers: {','.join(names)}", latency)

    return await _do()


async def probe_binary_attestation() -> ProbeResult:
    """Probe binary attestation: compare SHA-256 of an on-disk binary to a
    reference hash, and optionally compare a second file (e.g. a
    constitution) to a second reference hash.

    All paths are operator-configured via env vars:
      WATCHDOG_ATTESTATION_BINARY_PATH        — file to hash
      WATCHDOG_ATTESTATION_CONSTITUTION_PATH  — optional second file
      WATCHDOG_ATTESTATION_HASH_DIR           — directory containing
                                               reference hashes
    """
    import hashlib

    binary_path = os.environ.get("WATCHDOG_ATTESTATION_BINARY_PATH")
    if not binary_path:
        return ProbeResult(
            "P8_binary_attestation",
            Status.GREEN,
            "skipped (WATCHDOG_ATTESTATION_BINARY_PATH unset)",
            0.0,
        )

    constitution_path = os.environ.get("WATCHDOG_ATTESTATION_CONSTITUTION_PATH")
    hash_dir = os.environ.get("WATCHDOG_ATTESTATION_HASH_DIR", "/tmp/ali-ram-moat")

    binary_basename = Path(binary_path).name
    binary_hash_file = Path(hash_dir) / f"{binary_basename}.hash"
    constitution_hash_file = (
        Path(hash_dir) / f"{binary_basename}.constitution-hash"
        if constitution_path
        else None
    )

    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            if not Path(hash_dir).is_dir():
                return ProbeResult(
                    "P8_binary_attestation",
                    Status.GREEN,
                    "no attestation record (fresh boot)",
                    (time.perf_counter() - t0) * 1000,
                )

            mismatches = []

            if binary_hash_file.exists():
                expected = binary_hash_file.read_text().strip()
                actual = hashlib.sha256(Path(binary_path).read_bytes()).hexdigest()
                if expected and actual != expected:
                    mismatches.append(f"binary {actual[:8]}…≠{expected[:8]}…")
            else:
                mismatches.append(f"missing reference: {binary_hash_file.name}")

            if constitution_path and constitution_hash_file and constitution_hash_file.exists():
                expected_s = constitution_hash_file.read_text().strip()
                if Path(constitution_path).exists():
                    actual_s = hashlib.sha256(Path(constitution_path).read_bytes()).hexdigest()
                    if expected_s and actual_s != expected_s:
                        mismatches.append(f"constitution {actual_s[:8]}…≠{expected_s[:8]}…")
                else:
                    mismatches.append(f"missing file: {constitution_path}")

            if mismatches:
                return ProbeResult(
                    "P8_binary_attestation",
                    Status.RED,
                    "drift: " + ", ".join(mismatches),
                    (time.perf_counter() - t0) * 1000,
                )
            return ProbeResult(
                "P8_binary_attestation",
                Status.GREEN,
                "binary + constitution match",
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ProbeResult(
                "P8_binary_attestation",
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

    return await _do()


# ---------------------------------------------------------------------------
# Auto-fixers
# ---------------------------------------------------------------------------


def fix_binary_attestation() -> bool:
    """Clear the binary attestation record directory and (optionally) restart
    the consumer service.

    Generic — operates on whatever directory WATCHDOG_ATTESTATION_HASH_DIR
    points at. If WATCHDOG_ATTESTATION_RESTART_CMD is set, it's executed
    after the clear.
    """
    hash_dir = os.environ.get("WATCHDOG_ATTESTATION_HASH_DIR", "/tmp/ali-ram-moat")
    log.warning(f"auto-fix: clearing binary attestation ({hash_dir})")
    try:
        shutil.rmtree(hash_dir, ignore_errors=True)
    except Exception as e:
        log.error(f"auto-fix: failed to clear attestation: {e}")
        return False
    restart_cmd = os.environ.get("WATCHDOG_ATTESTATION_RESTART_CMD")
    if restart_cmd:
        try:
            subprocess.run(restart_cmd, shell=True, check=True, timeout=10, capture_output=True)
            log.warning(f"auto-fix: restart command succeeded: {restart_cmd}")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error(f"auto-fix: restart command failed: {e}")
            return False
    log.warning(
        f"auto-fix: attestation cleared. Set WATCHDOG_ATTESTATION_RESTART_CMD to\n"
        f"    consume the new seal automatically, or restart the consumer manually.\n"
        f"Until then, the running process is the previous binary and the next\n"
        f"auto-fix cycle will re-clear (idempotent, harmless)."
    )
    return True


def fix_deerflow_containers() -> bool:
    """Restart the deerflow PM2 app so the foreground docker compose re-attaches
    or recreates missing containers."""
    log.warning("auto-fix: pm2 restart deerflow")
    try:
        subprocess.run(["pm2", "restart", "deerflow"], check=True, timeout=30, capture_output=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("auto-fix: failed to pm2 restart deerflow: %s", e)
        return False


# ---------------------------------------------------------------------------
# Cycle orchestrator
# ---------------------------------------------------------------------------


@dataclass
class WatchdogState:
    cycle_id: int = 0
    consecutive_red: dict[str, int] = field(default_factory=dict)
    consecutive_yellow: dict[str, int] = field(default_factory=dict)


def dispatch_fixes(report: CycleReport, state: WatchdogState) -> None:
    """Apply safe auto-fixes based on RED probes. YELLOW probes are masked
    (logged but no fix)."""
    for probe in report.probes:
        if probe.status == Status.RED:
            state.consecutive_red[probe.name] = state.consecutive_red.get(probe.name, 0) + 1
        elif probe.status == Status.YELLOW:
            state.consecutive_yellow[probe.name] = state.consecutive_yellow.get(probe.name, 0) + 1
            state.consecutive_red[probe.name] = 0
        else:
            state.consecutive_red[probe.name] = 0
            state.consecutive_yellow[probe.name] = 0

    for probe in report.probes:
        if probe.status != Status.RED:
            continue
        # Only fix after 2 consecutive REDs (one cycle might be a transient blip)
        if state.consecutive_red[probe.name] < 2:
            continue
        if probe.name == "P8_binary_attestation":
            if fix_binary_attestation():
                probe.fixed = True
                probe.detail += " [attestation reset + restart issued]"
        elif probe.name == "P7_containers":
            if fix_deerflow_containers():
                probe.fixed = True
                probe.detail += " [pm2 restart deerflow issued]"
        else:
            log.warning("no auto-fix registered for %s — investigate", probe.name)


async def run_cycle(state: WatchdogState) -> CycleReport:
    state.cycle_id += 1
    t0 = time.perf_counter()
    report = CycleReport(cycle_id=state.cycle_id, started_at=t0, duration_ms=0.0)

    # (name, coroutine) pairs so we can label a probe correctly even if it
    # raises before producing its own ProbeResult.
    probes: list[tuple[str, asyncio.Task[ProbeResult] | asyncio.Future[ProbeResult]]] = [
        ("P1_nginx", asyncio.ensure_future(probe_nginx())),
        ("P2_gateway", asyncio.ensure_future(probe_gateway())),
        ("P3_frontend", asyncio.ensure_future(probe_frontend())),
        (
            "P4_local_llm_gateway",
            asyncio.ensure_future(probe_local_llm_gateway(port=int(os.environ.get("LOCAL_LLM_GATEWAY_PORT", "9000")))),
        ),
        (
            "P5_llama_loopback",
            asyncio.ensure_future(probe_llama_loopback(host=os.environ.get("LLAMA_HOST", "127.0.0.1"))),
        ),
        (
            "P6_llama_vram",
            asyncio.ensure_future(probe_llama_vram(host=os.environ.get("LLAMA_HOST", "127.0.0.1"))),
        ),
        ("P7_containers", asyncio.ensure_future(probe_containers())),
        ("P8_binary_attestation", asyncio.ensure_future(probe_binary_attestation())),
        (
            "P9_bridge",
            asyncio.ensure_future(probe_llama_bridge(host=os.environ.get("LLAMA_BRIDGE_HOST", "172.17.0.1"))),
        ),
    ]
    results = await asyncio.gather(*(c for _, c in probes), return_exceptions=True)
    for (name, _), result in zip(probes, results):
        if isinstance(result, BaseException):
            log.exception("probe %s raised", name)
            report.add(
                ProbeResult(
                    name=name,
                    status=Status.RED,
                    detail=f"probe raised: {type(result).__name__}: {result}",
                    latency_ms=0.0,
                )
            )
        else:
            report.add(result)

    dispatch_fixes(report, state)

    report.duration_ms = (time.perf_counter() - t0) * 1000
    if report.overall == Status.RED:
        report.exit_code = 1
    elif any(p.fixed for p in report.probes):
        # Auto-fix was applied; let supervisor see this clearly.
        log.warning("auto-fix applied this cycle; pm2 will continue to run us")
    return report


async def main_loop(args: argparse.Namespace) -> int:
    state = WatchdogState()
    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        stop.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    log.info("starting (interval=%ds, one-shot=%s)", args.interval, args.once)

    while not stop.is_set():
        try:
            # Per-cycle deadline (self-watchdog): a single hung probe (e.g.
            # llama_vram's 15s timeout during a network partition) shouldn't
            # be able to stall the watchdog beyond CYCLE_DEADLINE_SEC. If we
            # exceed it, treat the cycle as RED and force a restart via exit 1
            # so PM2 reaps us and comes back fresh.
            report = await asyncio.wait_for(run_cycle(state), timeout=CYCLE_DEADLINE_SEC)
        except asyncio.TimeoutError:
            log.error(
                "cycle exceeded %.0fs deadline — assuming hung; emitting RED report and exiting for PM2 restart",
                CYCLE_DEADLINE_SEC,
            )
            stuck_report = CycleReport(
                cycle_id=state.cycle_id + 1,
                started_at=time.time(),
                duration_ms=CYCLE_DEADLINE_SEC * 1000,
                overall=Status.RED,
                exit_code=1,
            )
            stuck_report.add(
                ProbeResult(
                    name="__watchdog_self__",
                    status=Status.RED,
                    detail=f"cycle exceeded {CYCLE_DEADLINE_SEC:.0f}s deadline (a probe hung)",
                )
            )
            print(json.dumps(stuck_report.to_dict()), flush=True)
            return 1  # PM2 restart
        except Exception:
            log.exception("cycle raised")
            await asyncio.sleep(args.interval)
            continue

        print(json.dumps(report.to_dict()), flush=True)

        if args.once:
            return report.exit_code

        # If a fix was applied, re-probe next cycle without waiting the full interval
        wait = min(args.interval, 5) if any(p.fixed for p in report.probes) else args.interval
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass

    log.info("shutting down")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova local-dev-stack watchdog (generic)")
    parser.add_argument("--interval", type=int, default=30, help="seconds between cycles")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error(f"--interval must be >= 1 (got {args.interval}); tighter loops would saturate the probe targets")
    if CYCLE_DEADLINE_SEC < args.interval:
        # The deadline must always exceed the cycle interval, otherwise the
        # watchdog would self-abort on every normal cycle.
        parser.error(
            f"HEALTHCHECK_CYCLE_DEADLINE_SEC ({CYCLE_DEADLINE_SEC}) must be >= --interval ({args.interval})"
        )
    try:
        return asyncio.run(main_loop(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
