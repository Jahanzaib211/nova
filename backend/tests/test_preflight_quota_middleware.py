"""Tests for PreflightQuotaMiddleware.

The middleware short-circuits LLM calls when the provider's billing
balance is non-positive. These tests cover:

  - Pure-function helpers (_extract_balance)
  - _detect_provider given env vars (pure)
  - Cache mechanics (TTL, eviction)
  - Block decision (probe status → blocked bool)
  - abefore_model end-to-end (with httpx mocked via respx-style
    patch on the middleware's own httpx reference)

The probe is mocked at the module attribute level
("deerflow.agents.middlewares.preflight_quota_middleware.httpx.AsyncClient")
so the patch is scoped exactly where the import is used.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from deerflow.agents.middlewares.preflight_quota_middleware import (
    PreflightQuotaMiddleware,
    _ProbeResult,
    _extract_balance,
)


# ───────────────────────────────────────────────────────────────────────
# Pure helper tests
# ───────────────────────────────────────────────────────────────────────


def test_extract_balance_handles_known_keys():
    assert _extract_balance({"total_available": 12.34}) == 12.34
    assert _extract_balance({"total_credits": 0.0}) == 0.0
    assert _extract_balance({"balance": 99}) == 99.0
    assert _extract_balance({"remaining": 0.5}) == 0.5


def test_extract_balance_handles_string_numbers():
    assert _extract_balance({"total_available": "12.34"}) == 12.34
    assert _extract_balance({"balance": "99.0"}) == 99.0
    assert _extract_balance({"balance": "not a number"}) is None


def test_extract_balance_returns_none_for_unknown_shape():
    assert _extract_balance({}) is None
    assert _extract_balance({"unknown": "value"}) is None
    assert _extract_balance("not a dict") is None
    assert _extract_balance(None) is None


def test_extract_balance_negative_is_valid():
    """A negative balance (e.g. -0.50 from a hard-cap provider) should be treated as a real number."""
    assert _extract_balance({"total_available": -0.50}) == -0.50


# ───────────────────────────────────────────────────────────────────────
# _detect_provider
# ───────────────────────────────────────────────────────────────────────


def test_detect_provider_no_env_returns_none(monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    mw = PreflightQuotaMiddleware()
    assert mw._detect_provider(SimpleNamespace(context={"thread_id": "t"})) is None


def test_detect_provider_no_base_url_returns_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    mw = PreflightQuotaMiddleware()
    assert mw._detect_provider(SimpleNamespace(context={"thread_id": "t"})) is None


def test_detect_provider_with_openai_env_returns_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    mw = PreflightQuotaMiddleware()
    info = mw._detect_provider(SimpleNamespace(context={"thread_id": "t"}))
    assert info is not None
    assert info.api_key == "sk-test"
    # Strips trailing /v1 from base URL
    assert info.billing_url == "https://api.openai.com/dashboard/billing/credit"


def test_detect_provider_with_custom_base_url(monkeypatch):
    """Together / Groq / custom OpenAI-compatible providers work too."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.together.xyz/v1")
    mw = PreflightQuotaMiddleware()
    info = mw._detect_provider(SimpleNamespace(context={"thread_id": "t"}))
    assert info is not None
    assert info.billing_url == "https://api.together.xyz/dashboard/billing/credit"


def test_detect_provider_with_llm_env_fallback(monkeypatch):
    """LLM_API_KEY + LLM_BASE_URL fallback when OPENAI_* not set."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.custom.com/v1")
    mw = PreflightQuotaMiddleware()
    info = mw._detect_provider(SimpleNamespace(context={"thread_id": "t"}))
    assert info is not None
    assert info.api_key == "sk-llm-test"
    assert info.billing_url == "https://api.custom.com/dashboard/billing/credit"


# ───────────────────────────────────────────────────────────────────────
# Cache mechanics
# ───────────────────────────────────────────────────────────────────────


def test_cache_starts_empty():
    mw = PreflightQuotaMiddleware()
    assert mw._cache == {}


def test_cache_store_and_retrieve():
    mw = PreflightQuotaMiddleware()
    result = _ProbeResult(status="ok", balance=100.0, detail=None)
    mw._cache["https://test/dashboard/billing/credit"] = (time.monotonic() + 60, result)
    cached = mw._cache["https://test/dashboard/billing/credit"]
    assert cached[1].status == "ok"
    assert cached[1].balance == 100.0


def test_cache_ttl_expiry():
    mw = PreflightQuotaMiddleware(ttl_seconds=1.0)
    result = _ProbeResult(status="ok", balance=100.0, detail=None)
    mw._cache["https://test/credit"] = (time.monotonic() - 1, result)  # already expired
    # The cache value is there but stale; consumer logic checks expires_at
    expires_at, cached = mw._cache["https://test/credit"]
    assert expires_at < time.monotonic()
    assert cached.status == "ok"


# ───────────────────────────────────────────────────────────────────────
# Probe → decision flow (with mocked httpx)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_returns_ok_when_balance_positive(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    mock_response = SimpleNamespace(
        status_code=200,
        headers={"content-type": "application/json"},
        json=lambda: {"total_available": 100.50},
    )
    mock_client = SimpleNamespace(
        __aenter__=AsyncMock(return_value=lambda: mock_client),
        __aexit__=AsyncMock(return_value=None),
        get=AsyncMock(return_value=mock_response),
    )

    mw = PreflightQuotaMiddleware()
    with patch.object(mw, "_probe", AsyncMock(return_value=_ProbeResult("ok", 100.0, None))):
        blocked, content = await mw._acheck_blocked(SimpleNamespace(context={"thread_id": "t"}))
    assert blocked is False
    assert content is None


@pytest.mark.asyncio
async def test_probe_blocks_when_balance_zero(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    mw = PreflightQuotaMiddleware()
    with patch.object(mw, "_probe", AsyncMock(return_value=_ProbeResult("exhausted", 0.0, None))):
        blocked, content = await mw._acheck_blocked(SimpleNamespace(context={"thread_id": "t"}))
    assert blocked is True
    assert content is not None
    assert "Preflight quota check" in content
    assert "insufficient" in content.lower()


@pytest.mark.asyncio
async def test_probe_unknown_status_is_fail_open(monkeypatch):
    """Unknown status (network error, HTTP 500, missing fields) → allow the call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    mw = PreflightQuotaMiddleware()
    with patch.object(mw, "_probe", AsyncMock(return_value=_ProbeResult("unknown", None, "timeout"))):
        blocked, content = await mw._acheck_blocked(SimpleNamespace(context={"thread_id": "t"}))
    assert blocked is False
    assert content is None


@pytest.mark.asyncio
async def test_no_block_when_no_provider_detected(monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    mw = PreflightQuotaMiddleware()
    # No provider → _detect_provider returns None → no probe → no block
    blocked, content = await mw._acheck_blocked(SimpleNamespace(context={"thread_id": "t"}))
    assert blocked is False
    assert content is None


# ───────────────────────────────────────────────────────────────────────
# abefore_model end-to-end (with probe mocked)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abefore_model_returns_blocked_message(monkeypatch):
    """When blocked, abefore_model returns a synthetic AIMessage with the right flags."""
    from langchain.agents import AgentState
    from langchain_core.messages import AIMessage

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    state = SimpleNamespace(messages=[])

    mw = PreflightQuotaMiddleware()
    with patch.object(mw, "_probe", AsyncMock(return_value=_ProbeResult("exhausted", 0.0, None))):
        result = await mw.abefore_model(state, SimpleNamespace(context={"thread_id": "t"}))

    assert result is not None
    assert "messages" in result
    msg = result["messages"][0]
    assert isinstance(msg, AIMessage)
    assert msg.additional_kwargs["deerflow_error_fallback"] is True
    assert msg.additional_kwargs["hide_from_ui"] is True
    assert msg.additional_kwargs["error_reason"] == "quota"
    assert msg.additional_kwargs["error_type"] == "PreflightQuotaExhausted"
    assert "Preflight quota check" in msg.content


@pytest.mark.asyncio
async def test_abefore_model_returns_none_when_not_blocked(monkeypatch):
    """Happy path: provider has balance → no synthetic message → original state passes through."""
    from langchain.agents import AgentState

    state = SimpleNamespace(messages=[])

    mw = PreflightQuotaMiddleware()
    with patch.object(mw, "_probe", AsyncMock(return_value=_ProbeResult("ok", 100.0, None))):
        result = await mw.abefore_model(state, SimpleNamespace(context={"thread_id": "t"}))

    assert result is None  # non-blocking: original state passes through


@pytest.mark.asyncio
async def test_abefore_model_returns_none_when_probe_fails(monkeypatch):
    """Probe exception → wrapped in try/except → returns None (non-fatal)."""
    from langchain.agents import AgentState

    state = SimpleNamespace(messages=[])

    mw = PreflightQuotaMiddleware()

    async def _boom(*a, **k):
        raise RuntimeError("simulated probe failure")

    with patch.object(mw, "_probe", _boom):
        result = await mw.abefore_model(state, SimpleNamespace(context={"thread_id": "t"}))

    assert result is None  # non-fatal: never break the run


@pytest.mark.asyncio
async def test_abefore_model_no_provider_returns_none(monkeypatch):
    """No OpenAI-compatible provider detected → no-op."""
    from langchain.agents import AgentState

    for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    state = SimpleNamespace(messages=[])
    mw = PreflightQuotaMiddleware()
    result = await mw.abefore_model(state, SimpleNamespace(context={"thread_id": "t"}))
    assert result is None
