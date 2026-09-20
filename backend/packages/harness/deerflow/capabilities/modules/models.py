"""Models: what the gateway can answer with, and a live probe per model."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _describe(m: Any, *, default: bool) -> dict[str, Any]:
    return {
        "name": m.name,
        "display_name": getattr(m, "display_name", None) or m.name,
        "provider": (getattr(m, "use", "") or "").split(":")[0],
        "model": getattr(m, "model", None),
        "default": default,
        "hidden": bool(getattr(m, "hidden", False)),
        "supports_thinking": bool(getattr(m, "supports_thinking", False)),
        "supports_vision": bool(getattr(m, "supports_vision", False)),
        "max_input_tokens": getattr(m, "max_input_tokens", None),
        "base_url": getattr(m, "base_url", None) or (getattr(m, "extra", {}) or {}).get("base_url"),
    }


async def _list(ctx: OpContext, inp: Empty) -> Items:
    from deerflow.config.app_config import get_app_config

    models = get_app_config().models
    items = [_describe(m, default=i == 0) for i, m in enumerate(models)]
    return Items(items=items, total=len(items))


class ProbeIn(BaseModel):
    name: str = Field(description="Model name from models.list")


class ProbeOut(BaseModel):
    name: str
    ok: bool
    latency_ms: int | None = None
    detail: str | None = None


async def _probe(ctx: OpContext, inp: ProbeIn) -> ProbeOut:
    """One tiny completion — "Check model" — so a misconfigured key or a
    stopped local server is reported here and not on the user's first message."""
    from deerflow.models.factory import create_chat_model

    started = time.monotonic()
    try:
        model = create_chat_model(name=inp.name)
        reply = await model.ainvoke("Reply with the single word: ok")
        text = getattr(reply, "content", reply)
        return ProbeOut(name=inp.name, ok=True, latency_ms=int((time.monotonic() - started) * 1000), detail=str(text)[:80])
    except Exception as exc:
        return ProbeOut(name=inp.name, ok=False, latency_ms=int((time.monotonic() - started) * 1000), detail=f"{type(exc).__name__}: {exc}"[:300])


async def _status() -> ModuleStatus:
    from deerflow.config.app_config import get_app_config

    n = len(get_app_config().models)
    return ModuleStatus(configured=n > 0, healthy=n > 0, detail=f"{n} model(s) configured")


MODULE = CapabilityModule(
    id="models",
    title="Models",
    config_key="models",
    description="Chat models the gateway can route to, with a live per-model probe.",
    status=_status,
    operations=[
        Operation(name="models.list", kind="read", input=Empty, output=Items, handler=_list, description="Configured models with provider, capabilities and context window; the first is the default."),
        Operation(name="models.probe", kind="execute", input=ProbeIn, output=ProbeOut, handler=_probe, description="Send one tiny completion to a model and report latency or the exact error."),
    ],
)
