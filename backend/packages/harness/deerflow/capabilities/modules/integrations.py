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
    registry = _registry()
    results = await registry.probe_all()
    # IntegrationStatus (integrations/health.py) is healthy/degraded/down/
    # unknown/disabled. "configured" was never a member — a dead string that
    # excluded nothing. "disabled" is a deliberate operator choice, so it never
    # counts against the module.
    #
    # "degraded" means alive-but-broken ("alive, /v1/models failed",
    # "reachable, API key not accepted"), so it must not read as healthy in
    # general. But some services are *expected* to sit degraded on a given
    # deployment — Mailcow with no MAILCOW_API_KEY, say — and pinning the gate
    # yellow forever teaches everyone to ignore the board, which is how the
    # sandbox image vanished unnoticed. Those are marked ``required: false``
    # in config: still probed, still showing their real status on the card,
    # just unable to make the module unhealthy.
    ok = ("healthy", "disabled")
    services = getattr(registry.config, "services", {}) or {}

    def _counts_against_module(result: Any) -> bool:
        status = getattr(getattr(result, "status", None), "value", result.status)
        if status in ok:
            return False
        service = services.get(getattr(result, "id", ""))
        return getattr(service, "required", True)

    bad = [r for r in results if _counts_against_module(r)]
    healthy_now = [r for r in results if getattr(getattr(r, "status", None), "value", r.status) in ok]
    detail = f"{len(healthy_now)}/{len(results)} healthy"
    if bad:
        detail += f", {len(bad)} required failing: " + ", ".join(sorted(getattr(r, "id", "?") for r in bad))[:120]
    elif len(healthy_now) != len(results):
        detail += f", {len(results) - len(healthy_now)} optional degraded"
    return ModuleStatus(configured=True, healthy=not bad, detail=detail)


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
