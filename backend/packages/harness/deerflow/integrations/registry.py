"""The registry: every integration Nova can reach, probed concurrently,
cached briefly so a Settings page refresh does not fan out to a dozen
services on every render.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from deerflow.config.integrations_config import IntegrationsConfig
from deerflow.integrations.adapters import Adapter, default_adapters
from deerflow.integrations.base import HttpIntegration, IntegrationClient, now_iso
from deerflow.integrations.clients import CLIENTS
from deerflow.integrations.health import HealthResult, IntegrationStatus

logger = logging.getLogger(__name__)


class IntegrationRegistry:
    def __init__(
        self,
        config: IntegrationsConfig,
        *,
        clients: list[IntegrationClient],
        adapters: list[Adapter] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self._clients: dict[str, IntegrationClient] = {c.id: c for c in clients}
        self._adapters = adapters or []
        self._transport = transport
        self._cache: list[HealthResult] | None = None
        self._last_probe_at = 0.0
        self._lock = asyncio.Lock()

    # -- helpers ---------------------------------------------------------

    def _now(self) -> float:
        return time.monotonic()

    def _http(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self.config.probe_timeout_seconds)
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=True)

    def ids(self) -> list[str]:
        return list(self._clients)

    @staticmethod
    def _errored(client: IntegrationClient, exc: BaseException) -> HealthResult:
        return HealthResult(
            id=client.id,
            kind=client.kind,
            display_name=client.display_name,
            status=IntegrationStatus.UNKNOWN,
            endpoint=client.endpoint,
            latency_ms=None,
            checked_at=now_iso(),
            detail=f"probe raised {exc.__class__.__name__}: {exc}",
        )

    async def _run_adapters(self) -> list[HealthResult]:
        out: list[HealthResult] = []
        for adapter in self._adapters:
            try:
                out.extend(await asyncio.to_thread(lambda a=adapter: list(a())))
            except Exception as exc:  # one broken adapter must not hide the rest
                logger.warning("integrations: adapter %s failed: %s", getattr(adapter, "__name__", adapter), exc)
        return out

    # -- API -------------------------------------------------------------

    async def probe(self, id: str, *, refresh: bool = False) -> HealthResult:
        if id not in self._clients:
            cached = next((r for r in (self._cache or []) if r.id == id), None)
            if cached is not None and not refresh:
                return cached
            if cached is None:
                raise KeyError(id)
            return cached
        client = self._clients[id]
        async with self._http() as http:
            try:
                result = await client.probe(http)
            except Exception as exc:
                result = self._errored(client, exc)
        if self._cache is not None:
            self._cache = [result if r.id == id else r for r in self._cache]
        return result

    async def probe_all(self, *, refresh: bool = False) -> list[HealthResult]:
        async with self._lock:
            fresh = self._cache is not None and (self._now() - self._last_probe_at) < self.config.probe_cache_seconds
            if fresh and not refresh:
                return list(self._cache or [])
            results: list[HealthResult] = []
            if self._clients:
                async with self._http() as http:
                    probed = await asyncio.gather(*(c.probe(http) for c in self._clients.values()), return_exceptions=True)
                for client, outcome in zip(self._clients.values(), probed, strict=True):
                    results.append(self._errored(client, outcome) if isinstance(outcome, BaseException) else outcome)
            results.extend(await self._run_adapters())
            self._cache = results
            self._last_probe_at = self._now()
            return list(results)


def build_registry(
    config: IntegrationsConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    include_extensions: bool = True,
) -> IntegrationRegistry:
    clients: list[IntegrationClient] = [CLIENTS.get(id, HttpIntegration)(id, svc) for id, svc in config.services.items()]
    return IntegrationRegistry(config, clients=clients, adapters=default_adapters() if include_extensions else [], transport=transport)
