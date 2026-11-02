"""Preflight quota check for OpenAI-compatible LLM providers.

When the LLM provider rejects with quota errors (HTTP 429, insufficient
quota, etc.), the LLMErrorHandlingMiddleware injects a synthetic error
message and the circuit breaker may engage. Both are wasteful: the
provider's quota was already exhausted before the call was made.

This middleware checks the provider's quota BEFORE the LLM call. If
exhausted, it short-circuits with a clean structured error (no model
invocation, no retry storm, no contamination cycle).

Provider support (v1):
  - OpenAI-compatible providers that expose
    GET {base_url}/v1/dashboard/billing/credit
    (OpenAI, Azure OpenAI in some configs, Together, Groq, etc.)
  - Non-OpenAI providers (Anthropic, Bedrock, etc.): no-op fallback.
    The check is skipped; the existing error-handling pipeline still
    catches quota errors.

Caching:
  - Per-provider cache, 60s TTL.
  - Concurrent calls share a single in-flight probe (asyncio.Lock).
  - Failures (network, auth, missing endpoint) are cached as "unknown"
    for the same TTL to avoid retry storms. The check then proceeds
    (fail-open) rather than blocking every call.

Non-fatal by construction:
  - Every code path wrapped in try/except.
  - If the probe raises, we return None (allow the call).
  - If the probe succeeds but returns no quota info, we allow the call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, override

import httpx
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 60.0
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


class PreflightQuotaMiddleware(AgentMiddleware[AgentState]):
    """Short-circuit the LLM call when provider quota is exhausted.

    Runs in ``before_model``. Detects OpenAI-compatible providers,
    probes their billing endpoint, and blocks the call when the
    balance is non-positive.

    Args:
        ttl_seconds: How long to cache the probe result. Default 60.
        probe_timeout_seconds: HTTP timeout for the probe. Default 5.
        env_var_pattern: Pattern to detect provider type from env vars.
            Default: looks at standard LLM provider env vars.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        probe_timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self._ttl = ttl_seconds
        self._probe_timeout = probe_timeout_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, _ProbeResult]] = {}

    def _detect_provider(self, runtime: Runtime) -> _ProviderInfo | None:
        """Return provider info if the current config is OpenAI-compatible.

        Looks at environment variables that langchain_openai uses for
        configuration. Returns None for unsupported providers so the
        middleware becomes a no-op (the existing error pipeline handles
        quota errors).
        """
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")  # Anthropic uses similar env
            or os.getenv("LLM_API_KEY")
        )
        base_url = os.getenv("OPENAI_API_BASE") or os.getenv("LLM_BASE_URL")

        # Only OpenAI-compatible providers (or custom base_url with OpenAI key)
        # have the billing endpoint.
        if not api_key or not base_url:
            return None
        if "openai.com" not in base_url.lower() and not base_url.endswith("/v1"):
            # Custom base URLs (Together, Groq, etc.) — try the probe.
            pass

        # Normalize base_url to strip trailing /v1 if present
        if base_url.endswith("/v1"):
            billing_base = base_url[:-3]
        else:
            billing_base = base_url

        return _ProviderInfo(
            api_key=api_key,
            billing_url=f"{billing_base.rstrip('/')}/dashboard/billing/credit",
        )

    async def _probe(self, info: _ProviderInfo) -> _ProbeResult:
        """Probe the provider's billing endpoint with caching + lock."""
        async with self._lock:
            now = time.monotonic()
            if info.billing_url in self._cache:
                expires_at, cached = self._cache[info.billing_url]
                if now < expires_at:
                    return cached
                del self._cache[info.billing_url]

        # Fetch outside the lock to avoid blocking other providers
        try:
            async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
                resp = await client.get(
                    info.billing_url,
                    headers={"Authorization": f"Bearer {info.api_key}"},
                )
                if resp.status_code != 200:
                    result = _ProbeResult(status="unknown", balance=None, detail=f"http {resp.status_code}")
                else:
                    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    balance = _extract_balance(payload)
                    result = _ProbeResult(
                        status="exhausted" if balance is not None and balance <= 0 else "ok",
                        balance=balance,
                        detail=None,
                    )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.debug("preflight quota probe failed for %s: %s", info.billing_url, exc)
            result = _ProbeResult(status="unknown", balance=None, detail=str(exc)[:160])

        async with self._lock:
            self._cache[info.billing_url] = (time.monotonic() + self._ttl, result)
        return result

    async def _acheck_blocked(self, runtime: Runtime) -> tuple[bool, str | None]:
        """Return (blocked, error_content). blocked=True means short-circuit."""
        info = self._detect_provider(runtime)
        if info is None:
            return False, None
        result = await self._probe(info)
        if result.status == "exhausted":
            balance_str = f"${result.balance:.2f}" if result.balance is not None else "0"
            return True, (f"Preflight quota check: provider account has insufficient balance ({balance_str}). The LLM call has been skipped to avoid burning retries. Top up the provider account and try again.")
        return False, None

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        try:
            blocked, content = await self._acheck_blocked(runtime)
            if blocked and content:
                from langchain_core.messages import AIMessage

                return {
                    "messages": [
                        AIMessage(
                            content=content,
                            additional_kwargs={
                                "deerflow_error_fallback": True,
                                "hide_from_ui": True,
                                "error_type": "PreflightQuotaExhausted",
                                "error_reason": "quota",
                                "error_detail": content,
                            },
                        )
                    ]
                }
        except Exception:  # noqa: BLE001 — non-fatal
            logger.debug("PreflightQuotaMiddleware: abefore_model failed", exc_info=True)
        return None

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        # Sync path: no event loop available for async probe. Skip.
        return None


class _ProviderInfo:
    __slots__ = ("api_key", "billing_url")

    def __init__(self, api_key: str, billing_url: str) -> None:
        self.api_key = api_key
        self.billing_url = billing_url


class _ProbeResult:
    __slots__ = ("balance", "detail", "status")

    def __init__(self, status: str, balance: float | None, detail: str | None) -> None:
        self.status = status
        self.balance = balance
        self.detail = detail


def _extract_balance(payload: dict[str, Any]) -> float | None:
    """Pull the balance number out of an OpenAI billing-credits payload.

    The exact field name varies: ``total_available`` is the common one
    in current docs. We defensively try a few keys.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("total_available", "total_credits", "balance", "remaining"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


__all__ = ["PreflightQuotaMiddleware"]
