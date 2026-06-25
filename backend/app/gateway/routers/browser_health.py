"""CDP (Chrome DevTools Protocol) + browser subsystem health endpoint.

Exposes the operational state of the browser/computer-control surface so
ops dashboards (and the frontend's Activity panel) can show "browser
degraded" badges before the user notices.

Returns:
  - cdp_reachable: bool — whether we can connect to a chromium CDP
    endpoint (any active sandbox).
  - latency_ms: float | None — round-trip time for the CDP probe.
  - open_circuits: int — count of per-thread circuit breakers in OPEN state.
  - circuit_states: dict[str, str] — {thread_id: state} snapshot (bounded).
  - last_check_at: float — unix timestamp of the most recent probe.

The endpoint is intentionally cheap: it uses the cached circuit-breaker
snapshot (no I/O) plus a fast TCP probe to the CDP websocket if a sandbox
is available. No browser launch, no Playwright connect.

Design choice: the response shape mirrors the v6 ``health`` endpoint at
``/api/health`` so dashboards can render both with the same widget.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

from fastapi import APIRouter, Response

from deerflow.sandbox import browser_circuit_breaker as cb
from deerflow.sandbox.metrics import render_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

# Cap how many circuit states we include in the response — protects the
# endpoint from huge payloads when many sandboxes are active.
_MAX_CIRCUIT_STATES_IN_RESPONSE = int(os.environ.get("DEERFLOW_HEALTH_MAX_CIRCUITS", "100"))

# Cache the last probe result so a dashboard refresh doesn't hammer CDP.
_last_probe: dict[str, Any] = {
    "cdp_reachable": None,
    "latency_ms": None,
    "last_check_at": 0.0,
    "cdp_url": None,
}


def _probe_cdp_once(timeout_s: float = 1.0) -> tuple[bool, float, str | None]:
    """Probe a single CDP endpoint. Returns (reachable, latency_ms, cdp_url).

    Walks the active sandboxes (if any) and tries to read the CDP URL.
    AIO sandboxes ship with a chromium whose CDP is exposed; local
    sandboxes don't have a browser — we return (False, ..., None).

    The probe is bounded — we don't actually open a WebSocket; just
    confirm the host:port answers (or refuses fast). For a real CDP
    websocket check the agent would call browser_check which does the
    full Playwright connect.
    """
    start = time.monotonic()
    try:
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        provider = get_sandbox_provider()
        # Iterate registered sandboxes (cheap — provider exposes ._sandboxes).
        sandboxes = getattr(provider, "_sandboxes", {}) or {}
        for sid, sandbox in list(sandboxes.items())[:5]:  # probe at most 5
            client = getattr(sandbox, "_client", None)
            if client is None:
                continue
            try:
                info = client.browser.get_info()
                cdp_url = getattr(getattr(info, "data", info), "cdp_url", None)
                if not cdp_url:
                    continue
                # Cheap reachability check: parse host:port, TCP connect with timeout.
                # We don't expect success (CDP is websocket, not raw TCP) but the
                # refused/timeout pattern tells us the endpoint is reachable.
                from urllib.parse import urlsplit

                parts = urlsplit(cdp_url)
                host = parts.hostname
                port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
                if not host:
                    continue
                try:
                    with socket.create_connection((host, port), timeout=timeout_s):
                        latency = (time.monotonic() - start) * 1000.0
                        return (True, round(latency, 2), cdp_url)
                except (socket.timeout, OSError):
                    # Connection refused or unreachable — record latency but mark unreachable.
                    latency = (time.monotonic() - start) * 1000.0
                    return (False, round(latency, 2), cdp_url)
            except Exception as e:
                logger.debug("cdp health probe failed for sandbox %s: %s", sid, e)
                continue
    except Exception as e:
        logger.debug("cdp health probe failed: %s", e)

    return (False, None, None)


def _maybe_refresh_probe(min_interval_s: float = 5.0) -> None:
    """Refresh the cached probe if it's stale."""
    now = time.time()
    if (now - _last_probe["last_check_at"]) < min_interval_s:
        return
    reachable, latency, cdp_url = _probe_cdp_once()
    _last_probe["cdp_reachable"] = reachable
    _last_probe["latency_ms"] = latency
    _last_probe["cdp_url"] = cdp_url
    _last_probe["last_check_at"] = now


@router.get("/health/browser")
async def browser_health(response: Response) -> dict[str, Any]:
    """Browser subsystem health.

    Returns 200 when the subsystem is operational OR degraded-but-recoverable.
    Returns 503 only when the circuit-breaker subsystem itself has tripped for
    EVERY active thread (i.e. CDP is dead across the fleet).
    """
    _maybe_refresh_probe()

    # Snapshot the circuit breakers — bounded for the response.
    all_states = cb.snapshot()
    states_bounded = dict(list(all_states.items())[:_MAX_CIRCUIT_STATES_IN_RESPONSE])
    open_circuits = sum(1 for s in all_states.values() if s == "open")

    # If every known circuit is open, the subsystem is in trouble.
    fleet_healthy = open_circuits < max(1, len(all_states))

    payload = {
        "status": "healthy" if fleet_healthy else "degraded",
        "cdp_reachable": _last_probe["cdp_reachable"],
        "latency_ms": _last_probe["latency_ms"],
        "last_check_at": _last_probe["last_check_at"],
        "open_circuits": open_circuits,
        "circuit_states": states_bounded,
        "truncated": len(all_states) > _MAX_CIRCUIT_STATES_IN_RESPONSE,
        "total_circuits": len(all_states),
    }

    if not fleet_healthy:
        response.status_code = 503

    return payload


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus-format metrics scrape endpoint.

    Renders the in-process registry built by ``deerflow.sandbox.metrics``.
    Kept here (not in the main ``/health``) so Prometheus scrapers can
    poll it on a different cadence than the health endpoint.
    """
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")