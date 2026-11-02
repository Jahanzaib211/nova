"""Runtime health probes for :class:`HealthServiceImpl`.

Why this exists
---------------
``HealthServiceImpl`` was constructed as ``HealthServiceImpl()`` — ``probes or
{}`` — so ``check_all()`` iterated an empty dict and returned
``healthy=True, probe_count=0``. Vacuously healthy: a report that can never
fail is worse than no report, because it looks like evidence. No router
imported it either, so nothing ever surfaced it.

These probes answer "can the gateway actually do its job right now?", which is
a different question from the host-level gates (disk, drift, CI) and from the
watchdog's black-box HTTP probes. Those check the system around the app; these
check the app's own dependencies from the inside.

Every probe:

- returns a ``ProbeStatus`` rather than raising, so one broken dependency
  cannot mask the others,
- is bounded by a timeout, because a health check that hangs is an outage of
  its own,
- degrades to ``healthy=True`` with an explanatory message when a subsystem is
  *deliberately* not configured. A probe that is permanently red for an
  unconfigured optional feature trains operators to ignore the dashboard —
  the same reasoning behind HEALTHCHECK_DISABLED_PROBES in the watchdog.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Bound every probe. Long enough for a cold connection pool, short enough that
#: the aggregate endpoint stays responsive when something is wedged.
PROBE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class ProbeStatus:
    """What HealthServiceImpl.check_all reads off a probe result."""

    healthy: bool
    message: str = ""


def _ok(message: str) -> ProbeStatus:
    return ProbeStatus(healthy=True, message=message)


def _bad(message: str) -> ProbeStatus:
    return ProbeStatus(healthy=False, message=message)


async def probe_database() -> ProbeStatus:
    """Can we actually execute a query?

    Deliberately runs `SELECT 1` rather than inspecting pool state: a pool can
    look healthy while every connection is blocked, which is exactly what the
    59 GB SQLite file produced — `database is locked` under a pool that
    reported itself fine.
    """
    try:
        from sqlalchemy import text

        from deerflow.persistence.engine import get_session_factory

        factory = get_session_factory()
        if factory is None:
            return _ok("no SQL backend configured (database.backend=memory)")

        async def _query() -> None:
            async with factory() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_query(), timeout=PROBE_TIMEOUT_SEC)
        return _ok("query ok")
    except TimeoutError:
        return _bad(f"no response within {PROBE_TIMEOUT_SEC:.0f}s — pool exhausted or locked")
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        return _bad(f"{type(exc).__name__}: {exc}")


def app_state_probe(state: Any, attribute: str, label: str) -> Callable[[], Awaitable[ProbeStatus]]:
    """Probe that an initialised runtime object is present on the app state.

    The state is passed in rather than looked up globally. The gateway builds
    these once during startup and exposes no module-level accessor for them, so
    a probe that tried to import one would either be dead code or reach for a
    private global.

    Their absence means startup partially failed, which otherwise surfaces much
    later as a confusing request-time error.
    """

    async def _probe() -> ProbeStatus:
        value = getattr(state, attribute, None) if state is not None else None
        if value is None:
            return _bad(f"{label} was not initialised at startup")
        return _ok(type(value).__name__)

    return _probe


def stream_bridge_probe(state: Any) -> Callable[[], Awaitable[ProbeStatus]]:
    """The SSE fan-out. Without it, runs execute but nothing reaches the UI.

    Lives on app.state (deps.py:200), like the checkpointer and store — there
    is no module-level accessor.
    """

    async def _probe() -> ProbeStatus:
        bridge = getattr(state, "stream_bridge", None) if state else None
        if bridge is None:
            return _bad("stream bridge not initialised — SSE cannot be served")
        return _ok(type(bridge).__name__)

    return _probe


async def probe_sandbox_provider() -> ProbeStatus:
    """Sandboxes are optional; report configuration, not reachability.

    Actually creating one costs a container start, which is far too expensive
    for a health endpoint. The watchdog's CDP probe covers live reachability.
    """
    try:
        from deerflow.config.app_config import get_app_config

        sandbox = getattr(get_app_config(), "sandbox", None)
        if sandbox is None:
            return _ok("no sandbox section configured")
        url = getattr(sandbox, "provisioner_url", None)
        return _ok(f"provisioner {url}" if url else "local container mode")
    except Exception as exc:  # noqa: BLE001
        return _bad(f"{type(exc).__name__}: {exc}")


def channels_probe(state: Any) -> Callable[[], Awaitable[ProbeStatus]]:
    """Zero enabled channels is a valid deployment, not a fault."""

    async def _probe() -> ProbeStatus:
        service = getattr(state, "channel_service", None) if state else None
        if service is None:
            return _ok("channel service not running")
        try:
            status = service.status() if hasattr(service, "status") else {}
            channels = (status or {}).get("channels", {})
            running = [n for n, m in channels.items() if m.get("running")]
            enabled = [n for n, m in channels.items() if m.get("enabled")]
            if enabled and not running:
                return _bad(f"enabled but not running: {', '.join(sorted(enabled))}")
            return _ok(f"{len(running)}/{len(enabled)} enabled channels running" if enabled else "no channels enabled")
        except Exception as exc:  # noqa: BLE001
            return _ok(f"not inspectable ({type(exc).__name__})")

    return _probe


def default_probes(state: Any = None) -> dict[str, Callable[[], Awaitable[Any]]]:
    """The probe set the gateway wires into HealthServiceImpl at startup.

    ``state`` is the FastAPI ``app.state``. Passing None yields only the probes
    that need no runtime objects, which is what an embedded or test caller gets.
    """
    probes: dict[str, Callable[[], Awaitable[Any]]] = {
        "database": probe_database,
        "sandbox_provider": probe_sandbox_provider,
    }
    if state is not None:
        probes["checkpointer"] = app_state_probe(state, "checkpointer", "checkpointer")
        probes["store"] = app_state_probe(state, "store", "store")
        probes["stream_bridge"] = stream_bridge_probe(state)
        probes["channels"] = channels_probe(state)
    return probes
