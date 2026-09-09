#!/usr/bin/env python3
"""nova-healthcheck — supervisor watchdog for the local dev stack.

Polls 12 probes every CYCLE_INTERVAL seconds and auto-fixes common breakage
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
  P10. nova-litellm proxy reachability (172.17.0.1:4000/v1/models — the
      unified gateway for Ollama cloud models)
  P14. SearXNG reachability (127.0.0.1:8088/healthz; skipped when no tool
       is bound to it) — the silent DDG fallback hides an outage otherwise
  P11. Dify stack health (127.0.0.1:8088/console/api/setup → step=finished;
      pm2 app nova-dify running the compose stack)
  P12. Cloudflare Tunnel — systemd unit active + public URL reachable.
      Two layers: (a) cloudflared-nova.service is active, (b) the URL in
      CLOUDFLARE_TUNNEL_URL (default https://nova.alilabsx.com/health)
      returns HTTP 200 from the public edge. Auto-fix: sudo systemctl
      restart cloudflared-nova.service. Requires sudoers NOPASSWD for
      systemctl on this unit (set up by the boot script).

Auto-fixes (only safe, reversible ones):
  - Attestation drift → clear the hash dir (and run WATCHDOG_ATTESTATION_RESTART_CMD
    if set)
  - Container count < 3 → pm2 restart deerflow
  - llama-server ECONNREFUSED at boot → wait one cycle, no action (cold load)
  - P9 llama-bridge dead → pm2 restart (re-register from ecosystem.config.js on drift)
  - P10 nova-litellm dead → pm2 restart (start from ecosystem.config.js if missing)
  - P11 Dify dead → pm2 restart nova-dify (start from ecosystem.config.js if missing)
  - P12 tunnel dead → sudo systemctl restart cloudflared-nova.service

Status:
  - Exits 0 every cycle when all probes are GREEN.
  - Exits 1 if a cycle exceeds CYCLE_DEADLINE_SEC (a probe hung); PM2 will
    restart, and the next cycle starts fresh.

Reads:
  - ${LOCAL_LLM_GATEWAY_HOST:-127.0.0.1}:${LOCAL_LLM_GATEWAY_PORT:-9000} for P4
  - ${LLAMA_HOST:-127.0.0.1}:8081 for P5/P6
  - ${LLAMA_BRIDGE_HOST:-172.17.0.1}:8081 for P9
  - ${LITELLM_HOST:-172.17.0.1}:${LITELLM_PORT:-4000} for P10
  - ${DIFY_HOST:-127.0.0.1}:${DIFY_PORT:-8088} for P11
  - ${CLOUDFLARE_TUNNEL_URL:-https://nova.alilabsx.com/health} for P12
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
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

import httpx

CYCLE_DEADLINE_SEC = float(os.environ.get("HEALTHCHECK_CYCLE_DEADLINE_SEC", "60"))

# A repair that can never succeed — a script path that no longer exists, a
# missing sudoers rule — must not spawn subprocesses forever. On 2026-08-12
# this daemon ran 281 failing auto-fix actions per minute (733 `pm2` + 725
# `sudo` spawns in 21 minutes), each pm2 invocation a fresh ~50-100MB Node
# process, and drove the machine into swap exhaustion until it froze.
#
# So: back off exponentially per probe after each failed repair, and once a
# repair has failed FIX_FAILURE_LIMIT times in a row, open a circuit breaker
# and stop attempting it. The breaker closes again as soon as the probe
# recovers on its own, so a genuinely transient failure still self-heals.
FIX_FAILURE_LIMIT = int(os.environ.get("HEALTHCHECK_FIX_FAILURE_LIMIT", "5"))
FIX_BACKOFF_MAX_CYCLES = int(
    os.environ.get("HEALTHCHECK_FIX_BACKOFF_MAX_CYCLES", "60")
)


def disabled_probes() -> set[str]:
    """Probe names switched off via HEALTHCHECK_DISABLED_PROBES (comma-separated).

    A probe for a service that has been retired would otherwise sit RED
    forever, and this daemon's whole value is being the operator's single pane
    of glass — permanently-red probes for things nobody intends to run train
    you to ignore it, which is how a real outage gets missed.
    """
    raw = os.environ.get("HEALTHCHECK_DISABLED_PROBES", "")
    return {name.strip() for name in raw.split(",") if name.strip()}

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


async def probe_drift() -> ProbeResult:
    """Escalate a red deployment-drift gate into the watchdog.

    drift-gate.py already detects containers built from a different commit than
    the repo, a config_version behind the example, a stale frontend build, and
    PM2 apps that are not running. But it only wrote a file the Nova Ops console
    renders -- so divergence sat there until somebody happened to look.

    This is the class of fault behind "I recreated the container and the project
    started breaking on its own": the running system stopped matching the
    repository and nothing said so. Reading the gate here puts it on the same
    pager as nginx being down.

    YELLOW rather than RED for a stale file: the gate not having run recently is
    a nova-gates problem, not evidence of drift.
    """
    name = "P13_drift"
    t0 = time.perf_counter()
    path = os.environ.get("NOVA_DRIFT_GATE_STATUS_PATH", "").strip()
    target = Path(path) if path else Path.home() / ".nova" / "gates" / "drift.json"
    latency = (time.perf_counter() - t0) * 1000

    try:
        payload = json.loads(target.read_text())
    except FileNotFoundError:
        return ProbeResult(name, Status.YELLOW, "no drift gate status file yet", latency)
    except (OSError, json.JSONDecodeError) as exc:
        return ProbeResult(name, Status.YELLOW, f"unreadable: {exc}", latency)

    age = time.time() - (payload.get("checked_at_epoch") or 0)
    if age > 3 * 900:  # three drift-gate intervals
        return ProbeResult(name, Status.YELLOW,
                           f"drift gate is stale ({age / 60:.0f} min old)", latency)

    overall = payload.get("overall")
    failing = [c.get("name") for c in payload.get("checks", [])
               if c.get("status") == "red"]
    if overall == "red":
        return ProbeResult(name, Status.RED,
                           f"deployment drift: {', '.join(failing) or 'see drift.json'}",
                           latency)
    if overall == "yellow":
        warn = [c.get("name") for c in payload.get("checks", [])
                if c.get("status") == "yellow"]
        return ProbeResult(name, Status.YELLOW,
                           f"{', '.join(warn) or 'see drift.json'}", latency)
    return ProbeResult(name, Status.GREEN, "no drift", latency)


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------
#
# The per-cycle JSON on stdout goes to a PM2 log that nothing reads back, so
# until now the only way to see watchdog state was `pm2 logs`. Nova Ops cannot
# shell into the box, so it had no view of the 12 probes at all.
#
# Mirror every cycle into a status file instead, using the same shape and the
# same atomic write-temp-then-rename as k8s/scripts/k3s-watchdog.py, so the
# console can reuse one staleness rule for both watchdogs. `checked_at_epoch`
# is the field the console ages against.


def status_path() -> Path:
    override = os.environ.get("HEALTHCHECK_STATUS_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".nova" / "gates" / "healthcheck.json"


def write_status(report: CycleReport, interval_sec: float) -> None:
    """Atomically publish the cycle for Nova Ops. Never fatal.

    A watchdog that dies because it could not write its own status file would
    be worse than one that is merely unobservable, so every failure here is
    logged and swallowed.
    """
    payload = report.to_dict()
    payload["checked_at_epoch"] = time.time()
    payload["ok"] = report.overall is not Status.RED
    payload["interval_sec"] = interval_sec

    try:
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory as the target so the rename is atomic (a rename across
        # filesystems is a copy, which a reader can observe half-written).
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=".healthcheck-", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            tmp = Path(handle.name)
        tmp.replace(path)
    except Exception:
        log.warning("could not write status file", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def timed(coro_factory: Callable[[], Awaitable[ProbeResult]]) -> ProbeResult:
    return await coro_factory()


def _http_probe(
    name: str,
    url: str,
    *,
    expected_status: tuple[int, ...] = (200,),
    timeout: float = 3.0,
    body_validator: Callable[[dict], bool] | None = None,
) -> ProbeResult:
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
                    return ProbeResult(
                        name, Status.RED, "body validator failed", latency
                    )
            return ProbeResult(name, Status.GREEN, f"HTTP {res.status_code}", latency)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.RequestError,
        ) as e:
            return ProbeResult(
                name,
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ProbeResult(
                name,
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

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


async def probe_searxng(port: int = 8088) -> ProbeResult:
    """Is the search backend `web_search` is bound to actually up?

    SearXNG was defined in compose, wired into `config.yaml` as `web_search`'s
    backend, and never started by the launcher — for weeks. Nothing caught it,
    because `web_search` falls back to DuckDuckGo silently: search kept working
    while the Recon card stayed red, and a red card in one panel is easy to
    scroll past. This is the check that would have said so out loud.

    Skipped, not failed, when this deployment does not point a tool at SearXNG:
    a probe that is permanently red on a deployment that never wanted the
    service teaches people to ignore the whole daemon.
    """
    if not _searxng_is_configured():
        return ProbeResult(
            "P14_searxng", Status.GREEN, "skipped (no tool bound to searxng)", 0.0
        )
    return await _http_probe("P14_searxng", f"http://localhost:{port}/healthz")


def _searxng_is_configured() -> bool:
    """Does config.yaml bind any tool to a searxng base_url?

    Deliberately a substring check rather than a YAML parse: this daemon starts
    before the venv on some hosts, and adding a dependency to answer one boolean
    is a worse trade than a slightly blunt read.
    """
    try:
        config = Path(__file__).resolve().parent.parent / "config.yaml"
        return "searxng" in config.read_text()
    except OSError:
        return False


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
                return ProbeResult(
                    "P2_gateway",
                    Status.GREEN,
                    f"auth-wall HTTP {res.status_code}",
                    latency,
                )
            if res.status_code == 200:
                return ProbeResult("P2_gateway", Status.GREEN, "HTTP 200", latency)
            return ProbeResult(
                "P2_gateway", Status.RED, f"unexpected HTTP {res.status_code}", latency
            )
        except Exception as e:
            return ProbeResult(
                "P2_gateway",
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

    return await _do()


async def probe_frontend(port: int = 2026) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"http://localhost:{port}/")
            latency = (time.perf_counter() - t0) * 1000
            if (
                res.status_code == 200
                and "html" in res.headers.get("content-type", "").lower()
            ):
                return ProbeResult(
                    "P3_frontend",
                    Status.GREEN,
                    f"HTTP 200 html={len(res.text)}b",
                    latency,
                )
            return ProbeResult(
                "P3_frontend",
                Status.RED,
                f"HTTP {res.status_code} ct={res.headers.get('content-type', '?')}",
                latency,
            )
        except Exception as e:
            return ProbeResult(
                "P3_frontend",
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

    return await _do()


async def probe_local_llm_gateway(port: int = 9000) -> ProbeResult:
    async def _do() -> ProbeResult:
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"http://localhost:{port}/health")
            latency = (time.perf_counter() - t0) * 1000
            if res.status_code != 200:
                return ProbeResult(
                    "P4_local_llm_gateway",
                    Status.RED,
                    f"HTTP {res.status_code}",
                    latency,
                )
            payload = res.json()
            backend = payload.get("backend", "?")
            if backend == "healthy":
                return ProbeResult(
                    "P4_local_llm_gateway", Status.GREEN, f"backend={backend}", latency
                )
            if backend == "unreachable":
                # llama-server cold or down — mask as yellow for 2 cycles
                return ProbeResult(
                    "P4_local_llm_gateway",
                    Status.YELLOW,
                    f"backend={backend} (llama cold?)",
                    latency,
                )
            return ProbeResult(
                "P4_local_llm_gateway", Status.RED, f"backend={backend}", latency
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return ProbeResult(
                "P4_local_llm_gateway",
                Status.RED,
                "ECONNREFUSED",
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ProbeResult(
                "P4_local_llm_gateway",
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

    return await _do()


async def probe_llama_loopback(
    host: str = "127.0.0.1", port: int = 8081
) -> ProbeResult:
    return await _http_probe(
        "P5_llama_loopback",
        f"http://{host}:{port}/v1/models",
        body_validator=lambda d: (
            isinstance(d.get("data"), list) and len(d["data"]) >= 1
        ),
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
        body_validator=lambda d: (
            isinstance(d.get("data"), list) and len(d["data"]) >= 1
        ),
    )


async def probe_litellm(host: str = "172.17.0.1", port: int = 4000) -> ProbeResult:
    """Probe the nova-litellm proxy on the docker bridge IP — the unified
    OpenAI-compatible gateway Nova's container uses for Ollama cloud models
    (host.docker.internal:4000). RED here means every litellm-routed model is
    unreachable even if Ollama itself is healthy."""
    return await _http_probe(
        "P10_litellm",
        f"http://{host}:{port}/v1/models",
        body_validator=lambda d: (
            isinstance(d.get("data"), list) and len(d["data"]) >= 1
        ),
    )


async def probe_dify(host: str = "127.0.0.1", port: int = 8088) -> ProbeResult:
    """Probe the Dify stack (pm2 app nova-dify, fork at ~/Desktop/dify) via
    its localhost-bound nginx. /console/api/setup returns step=finished on a
    healthy, initialized deployment; anything else means the api container
    (or the whole compose stack) is down or mid-migration."""
    return await _http_probe(
        "P11_dify",
        f"http://{host}:{port}/console/api/setup",
        body_validator=lambda d: d.get("step") == "finished",
    )


async def probe_llama_vram(
    host: str = "127.0.0.1", port: int = 8081, model: str = ""
) -> ProbeResult:
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
                    return ProbeResult(
                        "P6_llama_vram", Status.YELLOW, "no model advertised"
                    )
                model = models[0]["id"]
            except Exception as e:
                return ProbeResult(
                    "P6_llama_vram", Status.RED, f"models probe failed: {e}"
                )

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
                return ProbeResult(
                    "P6_llama_vram",
                    Status.GREEN,
                    f"completion OK model={model}",
                    latency,
                )
            if res.status_code in (503, 502, 500):
                return ProbeResult(
                    "P6_llama_vram",
                    Status.YELLOW,
                    f"VRAM cold HTTP {res.status_code}",
                    latency,
                )
            return ProbeResult(
                "P6_llama_vram", Status.RED, f"HTTP {res.status_code}", latency
            )
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            return ProbeResult(
                "P6_llama_vram",
                Status.YELLOW,
                "timeout (VRAM cold?)",
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ProbeResult(
                "P6_llama_vram",
                Status.RED,
                f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000,
            )

    return await _do()


async def probe_containers(
    project: str = "deer-flow-dev", min_count: int = 3
) -> ProbeResult:
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
            return ProbeResult(
                "P7_containers",
                Status.RED,
                "docker binary not found",
                (time.perf_counter() - t0) * 1000,
            )
        except asyncio.TimeoutError:
            return ProbeResult(
                "P7_containers",
                Status.RED,
                "docker ps timeout",
                (time.perf_counter() - t0) * 1000,
            )
        latency = (time.perf_counter() - t0) * 1000
        if proc.returncode != 0:
            return ProbeResult(
                "P7_containers",
                Status.RED,
                f"docker exit {proc.returncode}: {stderr.decode()[:80]}",
                latency,
            )
        names = [n for n in stdout.decode().splitlines() if n.strip()]
        if len(names) < min_count:
            return ProbeResult(
                "P7_containers",
                Status.RED,
                f"only {len(names)} containers (need ≥{min_count}): {names}",
                latency,
            )
        return ProbeResult(
            "P7_containers",
            Status.GREEN,
            f"{len(names)} containers: {','.join(names)}",
            latency,
        )

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

            if (
                constitution_path
                and constitution_hash_file
                and constitution_hash_file.exists()
            ):
                expected_s = constitution_hash_file.read_text().strip()
                if Path(constitution_path).exists():
                    actual_s = hashlib.sha256(
                        Path(constitution_path).read_bytes()
                    ).hexdigest()
                    if expected_s and actual_s != expected_s:
                        mismatches.append(
                            f"constitution {actual_s[:8]}…≠{expected_s[:8]}…"
                        )
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


async def probe_tunnel(public_url: str = "https://nova.alilabsx.com/health") -> ProbeResult:
    """Probe the Cloudflare Tunnel public-domain reachability.

    Two layers, two checks (both must be GREEN for an overall GREEN):
      1. systemd cloudflared-nova.service is active — proves the local
         connector is up and registered with Cloudflare's edge.
      2. The public URL (e.g. https://nova.alilabsx.com/health) returns
         HTTP 200 from the external Cloudflare edge → proves the route
         + DNS + proxy are wired end-to-end from a real client's
         perspective.

    Operators can override PUBLIC_URL via env (CLOUDFLARE_TUNNEL_URL) to
    point at any hostname they tunnel; the default is the hackathon demo
    domain nova.alilabsx.com → Nova gateway. If the public URL is empty
    or unset, only the systemd probe runs (useful for behind-the-firewall
    deployments that don't expose a public hostname).
    """
    public_url = os.environ.get("CLOUDFLARE_TUNNEL_URL", public_url).strip()
    t0 = time.perf_counter()

    # Layer 1: the local connector must be running — under systemd *or* PM2.
    #
    # This probe used to demand `cloudflared-nova.service`, but that unit was
    # never installed on this host: the tunnel runs as the PM2 app `tunnel-nova`
    # (see ecosystem.config.js). Layer 1 was therefore permanently red, so the
    # whole probe got switched off — and with P1/P2/P3 all terminating at
    # localhost:2026, nothing was left watching the public hostname at all. A
    # check that only recognises one deployment shape gets disabled rather than
    # fixed, and takes its layer-2 coverage down with it.
    connector = ""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "cloudflared-nova.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            connector = "systemd=active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not connector:
        try:
            proc = subprocess.run(
                ["pm2", "jlist"], capture_output=True, text=True, timeout=15
            )
            if proc.returncode == 0:
                for app in json.loads(proc.stdout or "[]"):
                    if app.get("name") == "tunnel-nova" and (
                        app.get("pm2_env", {}).get("status") == "online"
                    ):
                        connector = "pm2=online"
                        break
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

    if not connector:
        return ProbeResult(
            "P12_tunnel",
            Status.RED,
            "no cloudflared connector (systemd inactive and tunnel-nova not online)",
            (time.perf_counter() - t0) * 1000,
        )

    # Layer 2: public URL must respond (only if configured).
    if not public_url:
        return ProbeResult(
            "P12_tunnel",
            Status.GREEN,
            f"{connector} (CLOUDFLARE_TUNNEL_URL unset; layer-2 skipped)",
            (time.perf_counter() - t0) * 1000,
        )

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            res = await client.get(public_url)
        latency = (time.perf_counter() - t0) * 1000
        if res.status_code != 200:
            return ProbeResult(
                "P12_tunnel",
                Status.RED,
                f"{connector} edge={public_url} HTTP {res.status_code}",
                latency,
            )
        return ProbeResult(
            "P12_tunnel",
            Status.GREEN,
            f"{connector} edge HTTP 200 {latency:.0f}ms",
            latency,
        )
    except Exception as e:
        return ProbeResult(
            "P12_tunnel",
            Status.RED,
            f"{connector} edge={type(e).__name__}",
            (time.perf_counter() - t0) * 1000,
        )


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
            subprocess.run(
                restart_cmd, shell=True, check=True, timeout=10, capture_output=True
            )
            log.warning(f"auto-fix: restart command succeeded: {restart_cmd}")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error(f"auto-fix: restart command failed: {e}")
            return False
    log.warning(
        "auto-fix: attestation cleared. Set WATCHDOG_ATTESTATION_RESTART_CMD to\n"
        "    consume the new seal automatically, or restart the consumer manually.\n"
        "Until then, the running process is the previous binary and the next\n"
        "auto-fix cycle will re-clear (idempotent, harmless)."
    )
    return True


def _ecosystem_file() -> str:
    """Path to nova's pm2 ecosystem config — the source of truth for every
    app's script path."""
    return os.environ.get(
        "NOVA_ECOSYSTEM_FILE",
        str(Path(__file__).resolve().parent.parent / "ecosystem.config.js"),
    )


def _pm2_script_path(described_stdout: str) -> str | None:
    """Pull the 'script path' cell out of `pm2 describe` table output."""
    for line in described_stdout.splitlines():
        if "script path" in line and "│" in line:
            return line.split("│")[-2].strip()
    return None


def _heal_pm2_app(app: str, timeout: int = 30, check_drift: bool = False) -> bool:
    """Restart a pm2 app, or re-register it from ecosystem.config.js when it is
    missing — and, with ``check_drift``, when pm2's saved dump points at a
    script that no longer exists on disk (restarting that just resurrects
    orphaned stale code).

    Returns False on failure so the caller's circuit breaker can back off: a
    repair whose script path does not exist can never succeed, and retrying it
    every cycle is what exhausted this machine's memory on 2026-08-12.
    """
    ecosystem = _ecosystem_file()
    try:
        described = subprocess.run(
            ["pm2", "describe", app], timeout=15, capture_output=True, text=True
        )
        drifted = False
        if check_drift and described.returncode == 0:
            script_path = _pm2_script_path(described.stdout)
            drifted = script_path is not None and not Path(script_path).exists()
        if described.returncode != 0 or drifted:
            log.warning(
                "auto-fix: %s %s — re-registering from %s",
                app,
                "script path missing on disk" if drifted else "not registered",
                ecosystem,
            )
            if check_drift:
                subprocess.run(["pm2", "delete", app], timeout=15, capture_output=True)
            subprocess.run(
                ["pm2", "start", ecosystem, "--only", app],
                check=True,
                timeout=timeout,
                capture_output=True,
            )
            subprocess.run(["pm2", "save"], timeout=15, capture_output=True)
        else:
            log.warning("auto-fix: pm2 restart %s", app)
            subprocess.run(
                ["pm2", "restart", app],
                check=True,
                timeout=timeout,
                capture_output=True,
            )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as e:
        log.error("auto-fix: failed to heal %s: %s", app, e)
        return False


def fix_deerflow_containers() -> bool:
    """Heal a RED P7_containers by restarting the pm2 app that owns the
    deer-flow-dev compose stack.

    That app is named ``nova`` in ecosystem.config.js — it execs
    scripts/pm2-deerflow.sh, which brings the stack up with
    docker-compose.prod-frontend.yaml in the -f chain. Restarting it both
    recreates missing containers and puts the frontend back on the prod
    target. This previously restarted a pm2 app named "deerflow", which has
    never existed, so the repair could only ever fail — and did, every cycle,
    forever.
    """
    return _heal_pm2_app("nova", timeout=60)


def fix_llama_bridge() -> bool:
    """Heal the llama-bridge PM2 app for a RED P9_bridge.

    Two failure modes observed in production:
      1. Process crashed/hung — a plain ``pm2 restart`` clears it.
      2. Registration drift — pm2's saved dump points at a script path that no
         longer exists (e.g. after the bridge source moved repos), so the
         "online" process is orphaned stale code and restart just resurrects
         the wrong thing. In that case re-register from ecosystem.config.js,
         which is the source of truth for the script path, and persist.
    """
    return _heal_pm2_app("llama-bridge", check_drift=True)


def fix_litellm() -> bool:
    """Heal the nova-litellm PM2 app for a RED P10_litellm: restart, or
    re-register from ecosystem.config.js if the app is missing."""
    return _heal_pm2_app("nova-litellm")


def fix_dify() -> bool:
    """Heal the nova-dify PM2 app for a RED P11_dify: restart, or re-register
    from ecosystem.config.js if the app is missing. The pm2 app runs
    `docker compose up` in the foreground, so a restart reconciles the whole
    Dify compose stack. Longer timeout than the other fixes — a full stack
    bring-up is not instant."""
    return _heal_pm2_app("nova-dify", timeout=60)


def fix_tunnel() -> bool:
    """Heal cloudflared-nova.service for a RED P12_tunnel.

    Failure modes observed in production (2026-07-12):
      1. systemd unit crashed/inactive — `systemctl restart` brings it back.
      2. systemd in ``start-limit-hit`` state — `restart` does NOT clear
         this; you must run ``systemctl reset-failed`` first or the
         restart will be rejected. Without this, the auto-fix silently
         fails and the tunnel stays down (Error 1033 to end users).
      3. cloudflared up locally but public URL not reachable — DNS or
         Cloudflare-side route broke; we restart anyway because it's
         cheap and often works (e.g. QUIC reconnect after edge-IP
         rotation), and surface the detail to the operator.

    The reset-failed call requires ``sudo -n systemctl reset-failed
    cloudflared-nova.service`` — see sudoers block in
    ``install-cloudflared-nova.sh``. If the operator has not yet
    applied that sudoers entry, we surface a clear alarm here so
    Error 1033 doesn't recur silently.
    """
    # Step 0 — one-time check that the sudoers entry covers reset-failed.
    # Without it, the watchdog cannot clear start-limit-hit and Error 1033
    # recurs every time cloudflared quits cleanly. We don't block on this
    # (we still try restart — it works for fresh failures) but we WARN so
    # the operator sees it on every cycle.
    try:
        sudo_l = subprocess.run(
            ["sudo", "-n", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if sudo_l.returncode == 0 and "reset-failed cloudflared-nova" not in sudo_l.stdout:
            log.warning(
                "auto-fix: sudoers is missing 'reset-failed cloudflared-nova' — "
                "next start-limit-hit outage WILL NOT auto-recover. "
                "Re-run scripts/install-cloudflared-nova.sh as a sudo-capable user."
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Step 1 — clear any start-limit-hit state. restart() alone will not.
    log.warning("auto-fix: clearing start-limit-hit (if any) for cloudflared-nova")
    try:
        reset_proc = subprocess.run(
            ["sudo", "-n", "systemctl", "reset-failed", "cloudflared-nova.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if reset_proc.returncode != 0:
            # Not fatal — the unit may simply not be in failed state, OR
            # sudoers may not permit reset-failed (Step 0 warning).
            log.info(
                "auto-fix: reset-failed returned %d (stderr=%s) — likely already clean "
                "or sudoers missing",
                reset_proc.returncode,
                reset_proc.stderr.strip()[:200],
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("auto-fix: reset-failed preflight failed: %s", e)
        return False

    # Step 2 — restart (or start, depending on current state).
    log.warning("auto-fix: restarting cloudflared-nova.service")
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "cloudflared-nova.service"],
            check=True,
            timeout=30,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        log.error(
            "auto-fix: systemctl restart failed (%s). If sudo requires a "
            "password, run: sudo systemctl restart cloudflared-nova.service "
            "manually.",
            e,
        )
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("auto-fix: failed to restart tunnel: %s", e)
        return False

    # Step 3 — verify the systemd unit actually came up. restart() exits
    # 0 even if the unit subsequently crashes; we wait up to 10s for
    # the service to be active before claiming success.
    log.info("auto-fix: waiting for cloudflared-nova.service to become active")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            if subprocess.run(
                ["sudo", "-n", "systemctl", "is-active", "cloudflared-nova.service"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip() == "active":
                log.info("auto-fix: cloudflared-nova.service is active")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        time.sleep(0.5)
    log.error(
        "auto-fix: cloudflared-nova.service did not become active within 10s of restart — "
        "manual intervention required"
    )
    return False


# ---------------------------------------------------------------------------
# Cycle orchestrator
# ---------------------------------------------------------------------------


@dataclass
class WatchdogState:
    cycle_id: int = 0
    consecutive_red: dict[str, int] = field(default_factory=dict)
    consecutive_yellow: dict[str, int] = field(default_factory=dict)
    # Circuit-breaker bookkeeping, keyed by probe name.
    fix_failures: dict[str, int] = field(default_factory=dict)
    next_fix_cycle: dict[str, int] = field(default_factory=dict)
    fix_circuit_open: set[str] = field(default_factory=set)


# probe name -> (fix callable, suffix appended to probe.detail on success).
# The callables look their target up in module globals at call time so tests
# can monkeypatch e.g. `fix_dify` on the module and still be dispatched to.
FIX_DISPATCH: dict[str, tuple[Callable[[], bool], str]] = {
    "P8_binary_attestation": (
        lambda: fix_binary_attestation(),
        " [attestation reset + restart issued]",
    ),
    "P7_containers": (
        lambda: fix_deerflow_containers(),
        " [pm2 restart nova issued]",
    ),
    "P9_bridge": (lambda: fix_llama_bridge(), " [llama-bridge healed via pm2]"),
    "P10_litellm": (lambda: fix_litellm(), " [nova-litellm healed via pm2]"),
    "P11_dify": (lambda: fix_dify(), " [nova-dify healed via pm2]"),
    "P12_tunnel": (
        lambda: fix_tunnel(),
        " [systemctl reset-failed+restart issued, verified active]",
    ),
}


def _reset_fix_breaker(state: WatchdogState, name: str) -> None:
    """Forget a probe's repair history — called when it goes non-RED, so a
    probe that recovers on its own gets a clean slate next time it breaks."""
    state.fix_failures.pop(name, None)
    state.next_fix_cycle.pop(name, None)
    state.fix_circuit_open.discard(name)


def _record_fix_failure(state: WatchdogState, name: str) -> None:
    """Back off exponentially after a failed repair, and trip the breaker once
    a repair has failed FIX_FAILURE_LIMIT times in a row."""
    failures = state.fix_failures.get(name, 0) + 1
    state.fix_failures[name] = failures
    if failures >= FIX_FAILURE_LIMIT:
        state.fix_circuit_open.add(name)
        log.error(
            "auto-fix: giving up on %s after %d consecutive failures — circuit "
            "open, no further repair attempts until the probe recovers. This "
            "usually means the repair can never succeed (missing script path, "
            "missing sudoers rule). Investigate manually.",
            name,
            failures,
        )
        return
    backoff = min(2**failures, FIX_BACKOFF_MAX_CYCLES)
    state.next_fix_cycle[name] = state.cycle_id + backoff
    log.warning(
        "auto-fix: %s repair failed (%d/%d) — backing off %d cycles",
        name,
        failures,
        FIX_FAILURE_LIMIT,
        backoff,
    )


def dispatch_fixes(report: CycleReport, state: WatchdogState) -> None:
    """Apply safe auto-fixes based on RED probes. YELLOW probes are masked
    (logged but no fix)."""
    for probe in report.probes:
        if probe.status == Status.RED:
            state.consecutive_red[probe.name] = (
                state.consecutive_red.get(probe.name, 0) + 1
            )
        elif probe.status == Status.YELLOW:
            state.consecutive_yellow[probe.name] = (
                state.consecutive_yellow.get(probe.name, 0) + 1
            )
            state.consecutive_red[probe.name] = 0
        else:
            state.consecutive_red[probe.name] = 0
            state.consecutive_yellow[probe.name] = 0
            _reset_fix_breaker(state, probe.name)

    for probe in report.probes:
        if probe.status != Status.RED:
            continue
        # Only fix after 2 consecutive REDs (one cycle might be a transient blip)
        if state.consecutive_red[probe.name] < 2:
            continue
        fix = FIX_DISPATCH.get(probe.name)
        if fix is not None:
            fix_fn, detail_suffix = fix
            # A repair that keeps failing is throttled, then abandoned. We do
            # NOT mark a probe fixed without the repair reporting success, so
            # the dashboard stays RED until it is genuinely healthy again.
            if probe.name in state.fix_circuit_open:
                continue
            if state.cycle_id < state.next_fix_cycle.get(probe.name, 0):
                continue
            if fix_fn():
                probe.fixed = True
                probe.detail += detail_suffix
                _reset_fix_breaker(state, probe.name)
            else:
                _record_fix_failure(state, probe.name)
        else:
            # Warn once when the probe first becomes fix-eligible, then every
            # 20th cycle while it stays RED — not every 30s forever (the P9
            # regression sat in exactly that spam pattern for days, unread).
            streak = state.consecutive_red[probe.name]
            if streak == 2 or streak % 20 == 0:
                log.warning(
                    "no auto-fix registered for %s — investigate (RED for %d cycles)",
                    probe.name,
                    streak,
                )


def build_probe_factories() -> list[tuple[str, Callable[[], Awaitable[ProbeResult]]]]:
    """The probe registry, as (name, coroutine factory) pairs.

    Module-level so the probe set can be inspected without running a cycle.
    Tests previously asserted a hardcoded probe count, so adding one broke
    three unrelated tests with `assert 14 == 13` -- an error naming neither
    the probe nor the reason.
    """
    # (name, coroutine factory) pairs. The coroutine is only created for probes
    # that are enabled, so a disabled probe costs nothing and never emits a
    # "coroutine was never awaited" warning. Naming the probe here also lets us
    # label it correctly if it raises before producing its own ProbeResult.
    probe_factories: list[tuple[str, Callable[[], Awaitable[ProbeResult]]]] = [
        ("P1_nginx", probe_nginx),
        ("P2_gateway", probe_gateway),
        ("P3_frontend", probe_frontend),
        (
            "P4_local_llm_gateway",
            lambda: probe_local_llm_gateway(
                port=int(os.environ.get("LOCAL_LLM_GATEWAY_PORT", "9000"))
            ),
        ),
        (
            "P5_llama_loopback",
            lambda: probe_llama_loopback(
                host=os.environ.get("LLAMA_HOST", "127.0.0.1")
            ),
        ),
        (
            "P6_llama_vram",
            lambda: probe_llama_vram(host=os.environ.get("LLAMA_HOST", "127.0.0.1")),
        ),
        ("P7_containers", probe_containers),
        ("P13_drift", probe_drift),
        (
            "P14_searxng",
            lambda: probe_searxng(port=int(os.environ.get("SEARXNG_PORT", "8088"))),
        ),
        ("P8_binary_attestation", probe_binary_attestation),
        (
            "P9_bridge",
            lambda: probe_llama_bridge(
                host=os.environ.get("LLAMA_BRIDGE_HOST", "172.17.0.1")
            ),
        ),
        (
            "P10_litellm",
            lambda: probe_litellm(
                host=os.environ.get("LITELLM_HOST", "172.17.0.1"),
                port=int(os.environ.get("LITELLM_PORT", "4000")),
            ),
        ),
        (
            "P11_dify",
            lambda: probe_dify(
                host=os.environ.get("DIFY_HOST", "127.0.0.1"),
                port=int(os.environ.get("DIFY_PORT", "8088")),
            ),
        ),
        ("P12_tunnel", probe_tunnel),
    ]
    return probe_factories


async def run_cycle(state: WatchdogState) -> CycleReport:
    state.cycle_id += 1
    t0 = time.perf_counter()
    report = CycleReport(cycle_id=state.cycle_id, started_at=t0, duration_ms=0.0)

    probe_factories = build_probe_factories()
    skip = disabled_probes()
    probes: list[
        tuple[str, asyncio.Task[ProbeResult] | asyncio.Future[ProbeResult]]
    ] = [
        (name, asyncio.ensure_future(factory()))
        for name, factory in probe_factories
        if name not in skip
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
            report = await asyncio.wait_for(
                run_cycle(state), timeout=CYCLE_DEADLINE_SEC
            )
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
            write_status(stuck_report, args.interval)
            return 1  # PM2 restart
        except Exception:
            log.exception("cycle raised")
            await asyncio.sleep(args.interval)
            continue

        print(json.dumps(report.to_dict()), flush=True)
        write_status(report, args.interval)

        if args.once:
            return report.exit_code

        # If a fix was applied, re-probe next cycle without waiting the full interval
        wait = (
            min(args.interval, 5)
            if any(p.fixed for p in report.probes)
            else args.interval
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass

    log.info("shutting down")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nova local-dev-stack watchdog (generic)"
    )
    parser.add_argument(
        "--interval", type=int, default=30, help="seconds between cycles"
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single cycle and exit"
    )
    args = parser.parse_args()
    if args.interval < 1:
        parser.error(
            f"--interval must be >= 1 (got {args.interval}); tighter loops would saturate the probe targets"
        )
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
