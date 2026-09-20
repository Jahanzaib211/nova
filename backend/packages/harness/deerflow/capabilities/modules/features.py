"""Feature flags: the switches the frontend reads from
``/api/runtime/capabilities`` → ``features``, computed in one place."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from deerflow.capabilities.modules._common import Empty
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation

#: Every flag the platform knows. Frontend ``FeatureFlagKey`` mirrors this list
#: (pinned by the contract test on both sides).
FLAG_KEYS: tuple[str, ...] = ("jobs", "integrations", "email_marketing", "acp_agents", "capabilities", "runtimes")


def compute_flags(config: Any | None = None) -> dict[str, bool]:
    """Flags for ``config`` (default: the live app config).

    ``acp_agents`` comes from the ACP config module (loaded from the same
    file) unless the caller's config carries its own ``acp_agents`` mapping,
    so a router that already resolved a config gets flags for *that* config.
    """
    from deerflow.config.acp_config import get_acp_agents
    from deerflow.config.app_config import get_app_config

    cfg = config if config is not None else get_app_config()

    def enabled(section: str) -> bool:
        block = getattr(cfg, section, None)
        return bool(getattr(block, "enabled", False)) if block is not None else False

    acp = getattr(cfg, "acp_agents", None) if config is not None else get_acp_agents()
    return {
        "jobs": enabled("jobs"),
        "integrations": enabled("integrations"),
        "email_marketing": enabled("email_marketing"),
        "acp_agents": bool(acp),
        "capabilities": True,
        "runtimes": enabled("runtimes"),
    }


class FlagsOut(BaseModel):
    flags: dict[str, bool]


async def _get(ctx: OpContext, inp: Empty) -> FlagsOut:
    return FlagsOut(flags=compute_flags())


async def _status() -> ModuleStatus:
    on = [k for k, v in compute_flags().items() if v]
    return ModuleStatus(configured=True, healthy=True, detail=f"on: {', '.join(on) or 'none'}")


MODULE = CapabilityModule(
    id="features",
    title="Labs",
    description="Server-declared feature switches; each maps to a config.yaml section.",
    status=_status,
    operations=[
        Operation(name="features.get", kind="read", input=Empty, output=FlagsOut, handler=_get, description="Every feature flag and whether it is on."),
    ],
)
