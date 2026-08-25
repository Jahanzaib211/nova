"""Harness-side event seams for the gateway's computer WebSocket (WS-G).

The gateway owns the WebSocket surfaces; the harness only knows how to
*announce* facts. Two tiny, non-fatal registries live here:

- :func:`add_dev_server_listener` — fired on every dev-server status
  transition (starting/ready/crashed/error/stopped) so the panel learns
  within milliseconds instead of waiting for the next poll.
- :func:`emit_observation` / :func:`add_observation_listener` — fired for
  every structured sandbox.log line (tool, path, ts), letting the gateway
  push ``file_changed``-class signals that invalidate stale previews
  instantly.

Listeners are plain callbacks and MUST be cheap/non-fatal: every call site
wraps them in try/except, because an observability seam can never break the
run path. Cross-thread fan-in is the gateway's problem — callbacks may be
invoked from watchdog threads, so receivers marshal onto their own loop.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

DevServerListener = Callable[[dict[str, Any]], None]
ObservationListener = Callable[[dict[str, Any]], None]

_dev_server_listeners: list[DevServerListener] = []
_dev_server_lock = threading.Lock()

_observation_listeners: list[ObservationListener] = []
_observation_lock = threading.Lock()


def add_dev_server_listener(listener: DevServerListener) -> None:
    with _dev_server_lock:
        _dev_server_listeners.append(listener)


def remove_dev_server_listener(listener: DevServerListener) -> None:
    with _dev_server_lock:
        if listener in _dev_server_listeners:
            _dev_server_listeners.remove(listener)


def emit_dev_server_status(payload: dict[str, Any]) -> None:
    """Announce a dev-server transition. Never raises."""
    with _dev_server_lock:
        listeners = list(_dev_server_listeners)
    for listener in listeners:
        try:
            listener(payload)
        except Exception:  # noqa: BLE001 - observability must never break a run
            logger.warning("dev-server listener failed", exc_info=True)


def add_observation_listener(listener: ObservationListener) -> None:
    with _observation_lock:
        _observation_listeners.append(listener)


def remove_observation_listener(listener: ObservationListener) -> None:
    with _observation_lock:
        if listener in _observation_listeners:
            _observation_listeners.remove(listener)


def emit_observation(payload: dict[str, Any]) -> None:
    """Announce one structured sandbox.log observation. Never raises."""
    with _observation_lock:
        listeners = list(_observation_listeners)
    for listener in listeners:
        try:
            listener(payload)
        except Exception:  # noqa: BLE001 - observability must never break a run
            logger.warning("observation listener failed", exc_info=True)


def emit_channel(channel: str, payload: dict[str, Any]) -> None:
    """Announce a typed event on a named computer-ws channel. Never raises."""
    try:
        from deerflow.sandbox import computer_events as _self

        # Route through the same listener registries so the gateway needs one
        # wiring: observations ride the observation listeners with an explicit
        # channel marker.
        merged = {"channel": channel, **payload}
        for listener in list(_self._observation_listeners):
            try:
                listener(merged)
            except Exception:  # noqa: BLE001
                logger.warning("%s channel listener failed", channel, exc_info=True)
    except Exception:  # noqa: BLE001 - observability must never break a run
        logger.warning("emit_channel(%s) failed", channel, exc_info=True)
