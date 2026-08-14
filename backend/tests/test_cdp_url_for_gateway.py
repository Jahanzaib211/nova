"""Regression tests for ``_cdp_url_for_gateway`` — the CDP URL rewriter that
makes Playwright in the gateway connect to the AIO sandbox's Chromium.

Pinned by the 2026-08-14 audit: the function exists, but no test in the
suite asserts its behaviour for the three host categories — loopback (must
be rewritten to the sandbox's ``base_url`` host:port), routable (must pass
through unchanged), and the ``DEER_FLOW_SANDBOX_HOST`` fallback when no
sandbox is reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _fake_client(cdp_url: str | None):
    info = MagicMock()
    info.data.cdp_url = cdp_url
    client = MagicMock()
    client.browser.get_info.return_value = info
    return client


def _fake_sandbox(base_url: str | None):
    if base_url is None:
        return None
    return SimpleNamespace(base_url=base_url)


def test_loopback_host_is_rewritten_to_sandbox_base_url():
    from deerflow.sandbox.browser_check import _cdp_url_for_gateway

    client = _fake_client("ws://127.0.0.1:8080/chromium")
    sandbox = _fake_sandbox("http://192.168.200.2:4100")
    rewritten = _cdp_url_for_gateway(client, sandbox)
    assert rewritten == "ws://192.168.200.2:4100/chromium"


def test_routable_host_is_passed_through():
    from deerflow.sandbox.browser_check import _cdp_url_for_gateway

    client = _fake_client("ws://browser.internal:9222/devtools/browser")
    sandbox = _fake_sandbox("http://192.168.200.2:4100")
    rewritten = _cdp_url_for_gateway(client, sandbox)
    assert rewritten == "ws://browser.internal:9222/devtools/browser"


def test_loopback_falls_back_to_deer_flow_sandbox_host_env(monkeypatch):
    from deerflow.sandbox.browser_check import _cdp_url_for_gateway

    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "k3s-runtime.internal")
    client = _fake_client("ws://localhost:9222/devtools")
    # No sandbox base_url at all — falls back to env.
    rewritten = _cdp_url_for_gateway(client, None)
    assert rewritten == "ws://k3s-runtime.internal:9222/devtools"


def test_missing_cdp_url_returns_none():
    from deerflow.sandbox.browser_check import _cdp_url_for_gateway

    client = _fake_client(None)
    assert _cdp_url_for_gateway(client, _fake_sandbox("http://10.0.0.1:9000")) is None


def test_get_info_failure_returns_none():
    from deerflow.sandbox.browser_check import _cdp_url_for_gateway

    client = MagicMock()
    client.browser.get_info.side_effect = RuntimeError("boom")
    assert _cdp_url_for_gateway(client, _fake_sandbox("http://10.0.0.1:9000")) is None
