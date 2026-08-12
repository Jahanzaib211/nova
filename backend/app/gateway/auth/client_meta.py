"""Client-IP and User-Agent extraction for audit + rate-limit purposes.

Single source of truth for the operator network identity stamped onto
``admin_audit`` rows and consumed by the per-IP login rate limiter.

Trust model mirrors the production deployment behind nginx / Cloudflare:
``AUTH_TRUSTED_PROXIES`` is a comma-separated CIDR allowlist. When the
TCP peer is in the allowlist, ``X-Real-IP`` overrides ``request.client.host``.
The header is silently ignored when no allowlist is configured — closing
the header-spoof bypass in dev / direct-gateway mode.
"""

from __future__ import annotations

import logging
import os
from ipaddress import ip_address, ip_network

from fastapi import Request

logger = logging.getLogger(__name__)

_TRUSTED_PROXIES_ENV = "AUTH_TRUSTED_PROXIES"


def _trusted_proxies() -> list:
    """Lazily-resolved list of trusted proxy CIDR networks.

    Empty by default; populated from ``AUTH_TRUSTED_PROXIES``. Read live
    (no module-level cache) so env-var overrides and test
    ``monkeypatch.setenv`` take effect immediately.
    """
    raw = os.getenv(_TRUSTED_PROXIES_ENV, "").strip()
    if not raw:
        return []
    nets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ip_network(entry, strict=False))
        except ValueError:
            logger.warning("%s: ignoring invalid entry %r", _TRUSTED_PROXIES_ENV, entry)
    return nets


def get_client_ip(request: Request) -> str:
    """Extract the real client IP for rate limiting / audit logging.

    Returns ``"unknown"`` when the underlying socket has no peer address
    (some ASGI test clients omitted it). Falls back to the TCP peer
    whenever the trusted-proxy chain is empty.
    """
    peer_host = request.client.host if request.client else None

    trusted = _trusted_proxies()
    if trusted and peer_host:
        try:
            peer_ip = ip_address(peer_host)
            if any(peer_ip in net for net in trusted):
                real_ip = request.headers.get("x-real-ip", "").strip()
                if real_ip:
                    return real_ip
        except ValueError:
            # peer_host wasn't a parseable IP (e.g. "unknown") — fall through
            pass

    return peer_host or "unknown"


def get_client_user_agent(request: Request) -> str | None:
    """Return the raw ``User-Agent`` header, or None if absent.

    512-char truncation matches the ``admin_audit.actor_user_agent`` column
    width so the value always fits without a DB error.
    """
    ua = request.headers.get("user-agent", "").strip()
    if not ua:
        return None
    return ua[:512]
