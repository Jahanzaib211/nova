"""Integrations: the probed registry of external services, MCP servers,
skills and ACP agents Nova can reach."""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Items, section_enabled
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation

_registry_cache: tuple[str, Any] | None = None


def _registry():
    """Registry for the freshest ``integrations:`` config (rebuilt on change)."""
    global _registry_cache
    from deerflow.config.app_config import get_app_config
    from deerflow.integrations.registry import build_registry

    config = get_app_config().integrations
    source = config.model_dump_json()
    if _registry_cache is None or _registry_cache[0] != source:
        _registry_cache = (source, build_registry(config))
    return _registry_cache[1]


def _dump(result: Any) -> dict[str, Any]:
    d = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else dict(result)
    for k, v in list(d.items()):
        if hasattr(v, "value"):
            d[k] = v.value
    return d


class ListIn(BaseModel):
    refresh: bool = Field(default=False, description="Bypass the probe cache")


class ProbeIn(BaseModel):
    integration_id: str


class ItemOut(BaseModel):
    item: dict[str, Any]


async def _list(ctx: OpContext, inp: ListIn) -> Items:
    reg = _registry()
    if not reg.config.enabled:
        return Items(items=[], total=0)
    results = await reg.probe_all(refresh=inp.refresh)
    items = [_dump(r) for r in results]
    return Items(items=items, total=len(items))


async def _probe(ctx: OpContext, inp: ProbeIn) -> ItemOut:
    reg = _registry()
    if not reg.config.enabled:
        raise RuntimeError("integrations disabled")
    return ItemOut(item=_dump(await reg.probe(inp.integration_id, refresh=True)))


async def _status() -> ModuleStatus:
    if not section_enabled("integrations"):
        return ModuleStatus(configured=False, healthy=False, detail="integrations.enabled is false")
    results = await _registry().probe_all()
    bad = [r for r in results if getattr(getattr(r, "status", None), "value", r.status) not in ("healthy", "configured")]
    return ModuleStatus(configured=True, healthy=not bad, detail=f"{len(results) - len(bad)}/{len(results)} healthy")


MODULE = CapabilityModule(
    id="integrations",
    title="Integrations",
    flag="integrations",
    config_key="integrations",
    description="External services (model gateways, mail/CRM/helpdesk, search/crawl/browser, agent gateways) and their live health.",
    status=_status,
    operations=[
        Operation(name="integrations.list", kind="read", input=ListIn, output=Items, handler=_list, description="Every integration with status, endpoint, capabilities and latency."),
        Operation(name="integrations.probe", kind="execute", input=ProbeIn, output=ItemOut, handler=_probe, description="Re-probe one integration now."),
    ],
)
