"""Async HTTP client for the sandbox provisioner's read-only /api/infra/*
surface — pod/deployment/event listing, pod logs, and resource metrics.

Powers the admin-only /api/v1/admin/infra/* proxy layer
(routers/admin_infra.py), which nova-ops's dashboard reads through the
existing BFF proxy. Mirrors the request shape of
``deerflow.community.aio_sandbox.remote_backend.RemoteSandboxBackend``
(the sandbox-lifecycle caller) but async, since this is app-layer FastAPI
code, not harness code bound by the sync sandbox-provider interface.
"""

from __future__ import annotations

import httpx

_TIMEOUT_S = 15.0


class InfraClientError(Exception):
    """Raised when the provisioner's /api/infra/* surface is unreachable or errors."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def _get(provisioner_url: str, path: str, params: dict | None = None) -> dict:
    url = f"{provisioner_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise InfraClientError(502, f"Provisioner unreachable: {exc}") from exc

    if resp.status_code >= 400:
        raise InfraClientError(resp.status_code, f"Provisioner returned {resp.status_code}: {resp.text[:300]}")

    return resp.json()


async def list_pods(provisioner_url: str) -> dict:
    return await _get(provisioner_url, "/api/infra/pods")


async def list_deployments(provisioner_url: str) -> dict:
    return await _get(provisioner_url, "/api/infra/deployments")


async def list_events(provisioner_url: str, limit: int = 100) -> dict:
    return await _get(provisioner_url, "/api/infra/events", params={"limit": limit})


async def get_metrics(provisioner_url: str) -> dict:
    return await _get(provisioner_url, "/api/infra/metrics")


async def get_pod_logs(provisioner_url: str, pod_name: str, tail: int = 200, container: str | None = None) -> dict:
    params: dict = {"tail": tail}
    if container:
        params["container"] = container
    return await _get(provisioner_url, f"/api/infra/pods/{pod_name}/logs", params=params)
