"""Tests for AuthRateLimitMiddleware._client_ip trust behavior.

The middleware must NOT honor ``X-Forwarded-For`` from arbitrary peers — only
from trusted proxies listed in ``AUTH_TRUSTED_PROXIES``. This mirrors the
contract already enforced by ``app.gateway.auth.client_meta.get_client_ip``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from app.gateway.auth_rate_limit_middleware import (
    AuthRateLimitMiddleware,
    _ip_in_trusted_set,
    _trusted_proxy_cidrs,
)


def _make_request(peer: str | None, xff: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": (peer, 0) if peer is not None else None,
    }
    if xff is not None:
        scope["headers"] = [(b"x-forwarded-for", xff.encode("latin1"))]
    req = Request(scope)
    return req


def _resolve(monkeypatch, *, trusted: str | None, peer: str | None, xff: str | None = None) -> str:
    if trusted is not None:
        monkeypatch.setenv("AUTH_TRUSTED_PROXIES", trusted)
    else:
        monkeypatch.delenv("AUTH_TRUSTED_PROXIES", raising=False)

    mw = AuthRateLimitMiddleware(MagicMock())
    req = _make_request(peer, xff)
    return mw._client_ip(req)


def test_xff_ignored_when_no_trusted_proxy(monkeypatch):
    ip = _resolve(monkeypatch, trusted=None, peer="10.0.0.1", xff="1.2.3.4")
    assert ip == "10.0.0.1"


def test_xff_honored_from_trusted_proxy(monkeypatch):
    ip = _resolve(monkeypatch, trusted="127.0.0.0/8", peer="127.0.0.1", xff="1.2.3.4")
    assert ip == "1.2.3.4"


def test_xff_rejected_from_untrusted_peer(monkeypatch):
    ip = _resolve(monkeypatch, trusted="10.0.0.0/8", peer="127.0.0.1", xff="1.2.3.4")
    assert ip == "127.0.0.1"


def test_xff_honors_first_hop_only(monkeypatch):
    ip = _resolve(monkeypatch, trusted="127.0.0.0/8", peer="127.0.0.1", xff="1.2.3.4, 5.6.7.8")
    assert ip == "1.2.3.4"


def test_invalid_trusted_proxy_entry_skipped(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        ip = _resolve(monkeypatch, trusted="not-a-cidr,127.0.0.0/8", peer="127.0.0.1", xff="1.2.3.4")
    assert ip == "1.2.3.4"


def test_no_client_returns_unknown(monkeypatch):
    ip = _resolve(monkeypatch, trusted=None, peer=None, xff=None)
    assert ip == "unknown"


def test_trusted_proxy_cidrs_parses_valid(monkeypatch):
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "10.0.0.0/8,192.168.0.0/16")
    cidrs = _trusted_proxy_cidrs()
    assert len(cidrs) == 2


def test_trusted_proxy_cidrs_skips_invalid(monkeypatch):
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "not-a-cidr,,10.0.0.0/8")
    cidrs = _trusted_proxy_cidrs()
    assert len(cidrs) == 1


def test_ip_in_trusted_set_match():
    import ipaddress

    cidrs = (ipaddress.ip_network("10.0.0.0/8"),)
    assert _ip_in_trusted_set("10.5.6.7", cidrs) is True
    assert _ip_in_trusted_set("192.168.1.1", cidrs) is False
    assert _ip_in_trusted_set("not-an-ip", cidrs) is False
