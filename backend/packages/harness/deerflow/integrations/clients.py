"""Per-service probes. Each knows its real health endpoint and what a
"working" answer looks like, so the card says "4 models" or "v2026-08",
not just "200".

Keyed by service id in ``CLIENTS``; anything else is a plain ``HttpIntegration``
against ``health_path`` (default ``/``).
"""

from __future__ import annotations

import httpx

from deerflow.integrations.base import HttpIntegration
from deerflow.integrations.health import HealthResult, IntegrationStatus


class OllamaIntegration(HttpIntegration):
    health_path = "/api/tags"

    async def interpret(self, client: httpx.AsyncClient, response: httpx.Response, latency_ms: float) -> HealthResult:
        if response.status_code != 200:
            return self._result(IntegrationStatus.DOWN, f"HTTP {response.status_code}", latency_ms)
        body = self._json(response) or {}
        models = [m.get("name", "?") for m in body.get("models", [])] if isinstance(body, dict) else []
        return self._result(IntegrationStatus.HEALTHY, f"{len(models)} models", latency_ms, capabilities=models)


class LiteLLMIntegration(HttpIntegration):
    health_path = "/health/liveliness"
    auth_header = "Authorization"

    def headers(self) -> dict[str, str]:
        key = self.config.resolve_api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def interpret(self, client: httpx.AsyncClient, response: httpx.Response, latency_ms: float) -> HealthResult:
        if response.status_code != 200:
            return self._result(IntegrationStatus.DOWN, f"HTTP {response.status_code}", latency_ms)
        try:
            models_resp = await self.get(client, "/v1/models")
        except httpx.HTTPError as exc:
            return self._result(IntegrationStatus.DEGRADED, f"alive, /v1/models failed: {exc}", latency_ms)
        if models_resp.status_code != 200:
            return self._result(IntegrationStatus.DEGRADED, f"alive, /v1/models HTTP {models_resp.status_code}", latency_ms)
        body = self._json(models_resp) or {}
        models = [m.get("id", "?") for m in body.get("data", [])] if isinstance(body, dict) else []
        return self._result(IntegrationStatus.HEALTHY, f"{len(models)} models", latency_ms, capabilities=models)


class MailcowIntegration(HttpIntegration):
    health_path = "/api/v1/get/status/version"
    ok_codes = (200, 401)
    _with_key = ("mailbox", "alias", "dkim", "domain")

    async def interpret(self, client: httpx.AsyncClient, response: httpx.Response, latency_ms: float) -> HealthResult:
        env = self.config.api_key_env or "MAILCOW_API_KEY"
        if response.status_code == 401 or (response.status_code == 200 and not self.config.resolve_api_key()):
            return self._result(IntegrationStatus.DEGRADED, f"reachable, API key not accepted or {env} not set", latency_ms, capabilities=[])
        if response.status_code != 200:
            return self._result(IntegrationStatus.DOWN, f"HTTP {response.status_code}", latency_ms)
        body = self._json(response) or {}
        version = body.get("version", "?") if isinstance(body, dict) else "?"
        return self._result(IntegrationStatus.HEALTHY, f"v{version}", latency_ms, capabilities=list(self._with_key))


class ChatwootIntegration(HttpIntegration):
    health_path = "/api"
    auth_header = "api_access_token"


class TwentyIntegration(HttpIntegration):
    health_path = "/healthz"
    auth_header = "Authorization"

    def headers(self) -> dict[str, str]:
        key = self.config.resolve_api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}


class OpenClawIntegration(HttpIntegration):
    health_path = "/"
    ok_codes = (200, 401, 403)


class SearxngIntegration(HttpIntegration):
    health_path = "/healthz"


class Crawl4AIIntegration(HttpIntegration):
    health_path = "/health"


class BrowserlessIntegration(HttpIntegration):
    health_path = "/pressure"
    ok_codes = (200, 401, 403)


CLIENTS: dict[str, type[HttpIntegration]] = {
    "ollama": OllamaIntegration,
    "litellm": LiteLLMIntegration,
    "mailcow": MailcowIntegration,
    "chatwoot": ChatwootIntegration,
    "twenty": TwentyIntegration,
    "openclaw": OpenClawIntegration,
    "searxng": SearxngIntegration,
    "crawl4ai": Crawl4AIIntegration,
    "browserless": BrowserlessIntegration,
}
