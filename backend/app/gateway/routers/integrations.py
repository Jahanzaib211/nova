"""``/api/integrations`` — what Nova can reach, and whether it works.

Read model over :mod:`deerflow.integrations`: services from
``integrations.services`` in config.yaml plus the MCP servers, skills and
ACP agents the gateway already knows. Results are cached for
``integrations.probe_cache_seconds``; ``?refresh=1`` or ``POST …/probe``
bypass the cache.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_auth
from app.gateway.deps import get_integrations_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class IntegrationItem(BaseModel):
    """One integration in the contract's ``item_schema`` shape."""

    id: str
    kind: str
    display_name: str
    endpoint: str | None = None
    status: str
    latency_ms: float | None = None
    checked_at: str | None = None
    detail: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class IntegrationsListResponse(BaseModel):
    enabled: bool
    probe_cache_seconds: float
    integrations: list[IntegrationItem]


def _item(result: Any) -> IntegrationItem:
    return IntegrationItem(**result.to_dict())


@router.get("", response_model=IntegrationsListResponse, summary="List every integration with its last probe result")
@require_auth
async def list_integrations(
    request: Request,
    refresh: bool = Query(default=False, description="Bypass the probe cache."),
) -> IntegrationsListResponse:
    registry = get_integrations_registry(request)
    if not registry.config.enabled:
        return IntegrationsListResponse(enabled=False, probe_cache_seconds=registry.config.probe_cache_seconds, integrations=[])
    results = await registry.probe_all(refresh=refresh)
    return IntegrationsListResponse(
        enabled=True,
        probe_cache_seconds=registry.config.probe_cache_seconds,
        integrations=[_item(r) for r in results],
    )


@router.get("/{integration_id}", response_model=IntegrationItem, summary="One integration's last probe result")
@require_auth
async def get_integration(integration_id: str, request: Request) -> IntegrationItem:
    registry = get_integrations_registry(request)
    if not registry.config.enabled:
        raise HTTPException(status_code=404, detail="Integrations are disabled")
    results = await registry.probe_all()
    for result in results:
        if result.id == integration_id:
            return _item(result)
    raise HTTPException(status_code=404, detail=f"Unknown integration {integration_id!r}")


@router.post("/{integration_id}/probe", response_model=IntegrationItem, summary="Probe one integration now")
@require_auth
async def probe_integration(integration_id: str, request: Request) -> IntegrationItem:
    registry = get_integrations_registry(request)
    if not registry.config.enabled:
        raise HTTPException(status_code=404, detail="Integrations are disabled")
    try:
        result = await registry.probe(integration_id, refresh=True)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown integration {integration_id!r}") from None
    return _item(result)
