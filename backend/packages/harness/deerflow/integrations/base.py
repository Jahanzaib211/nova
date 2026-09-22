"""Integration client protocol and the HTTP base most services share."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

import httpx

from deerflow.config.integrations_config import IntegrationServiceConfig
from deerflow.integrations.health import HealthResult, IntegrationKind, IntegrationStatus


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class IntegrationClient(Protocol):
    id: str
    kind: IntegrationKind
    display_name: str
    endpoint: str | None

    async def probe(self, client: httpx.AsyncClient) -> HealthResult: ...


class HttpIntegration:
    """GET one path, judge the status code. Subclasses refine ``interpret``.

    ``probe`` never raises: transport failures become ``down`` with the
    reason in ``detail`` so the card can say "connection refused" instead
    of "unknown".
    """

    health_path = "/"
    ok_codes: tuple[int, ...] = (200,)
    auth_header = "X-API-Key"

    def __init__(self, id: str, config: IntegrationServiceConfig):
        self.id = id
        self.config = config
        self.kind = IntegrationKind(config.kind)
        self.display_name = config.display_name or id
        self.endpoint = config.url.rstrip("/")

    # -- helpers ---------------------------------------------------------

    def _result(self, status: IntegrationStatus, detail: str | None, latency_ms: float | None, capabilities: list[str] | None = None) -> HealthResult:
        return HealthResult(
            id=self.id,
            kind=self.kind,
            display_name=self.display_name,
            status=status,
            endpoint=self.endpoint,
            latency_ms=latency_ms,
            checked_at=now_iso(),
            detail=detail,
            capabilities=list(capabilities if capabilities is not None else self.config.capabilities),
        )

    def headers(self) -> dict[str, str]:
        key = self.config.resolve_api_key()
        return {self.auth_header: key} if key else {}

    def path(self) -> str:
        return self.config.health_path or self.health_path

    async def get(self, client: httpx.AsyncClient, path: str) -> httpx.Response:
        return await client.get(self.endpoint + path, headers=self.headers())

    # -- protocol --------------------------------------------------------

    async def probe(self, client: httpx.AsyncClient) -> HealthResult:
        if not self.config.enabled:
            return self._result(IntegrationStatus.DISABLED, "disabled in config", None)
        t0 = time.perf_counter()
        try:
            response = await self.get(client, self.path())
        except httpx.TimeoutException:
            return self._result(IntegrationStatus.DOWN, f"timed out after {client.timeout.read}s", (time.perf_counter() - t0) * 1000)
        except httpx.HTTPError as exc:
            return self._result(IntegrationStatus.DOWN, str(exc) or exc.__class__.__name__, (time.perf_counter() - t0) * 1000)
        latency = (time.perf_counter() - t0) * 1000
        return await self.interpret(client, response, latency)

    async def interpret(self, client: httpx.AsyncClient, response: httpx.Response, latency_ms: float) -> HealthResult:
        if response.status_code in self.ok_codes:
            return self._result(IntegrationStatus.HEALTHY, f"HTTP {response.status_code}", latency_ms)
        return self._result(IntegrationStatus.DOWN, f"HTTP {response.status_code}", latency_ms)

    @staticmethod
    def _json(response: httpx.Response) -> dict | list | None:
        try:
            return response.json()
        except ValueError:
            return None
