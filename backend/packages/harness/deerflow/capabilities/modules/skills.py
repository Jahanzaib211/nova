"""Skills: the SKILL.md catalogue the lead agent can activate."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, Ok
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _storage():
    from deerflow.config.app_config import get_app_config
    from deerflow.skills.storage import get_or_new_skill_storage

    return get_or_new_skill_storage(app_config=get_app_config())


def _describe(s: Any) -> dict[str, Any]:
    return {
        "name": s.name,
        "description": getattr(s, "description", ""),
        "category": getattr(getattr(s, "category", None), "value", getattr(s, "category", None)),
        "enabled": bool(getattr(s, "enabled", True)),
        "path": str(getattr(s, "path", "")),
    }


async def _list(ctx: OpContext, inp: Empty) -> Items:
    skills = _storage().load_skills(enabled_only=False)
    items = [_describe(s) for s in skills]
    return Items(items=items, total=len(items))


class ToggleIn(BaseModel):
    name: str = Field(description="Skill name")
    enabled: bool


async def _toggle(ctx: OpContext, inp: ToggleIn) -> Ok:
    from deerflow.config.extensions_config import SkillStateConfig, get_extensions_config, persist_extensions_config

    known = {s.name for s in _storage().load_skills(enabled_only=False)}
    if inp.name not in known:
        raise LookupError(f"skill {inp.name!r} not found")
    cfg = get_extensions_config()
    cfg.skills[inp.name] = SkillStateConfig(enabled=inp.enabled)
    persist_extensions_config(cfg)
    try:
        from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async

        await refresh_skills_system_prompt_cache_async()
    except Exception:  # prompt cache is an optimisation; the file is the truth
        pass
    return Ok(ok=True, detail=f"{inp.name} {'enabled' if inp.enabled else 'disabled'}")


async def _status() -> ModuleStatus:
    try:
        n = len(_storage().load_skills(enabled_only=False))
    except Exception as exc:
        return ModuleStatus(configured=False, healthy=False, detail=f"skills unreadable: {exc}")
    return ModuleStatus(configured=True, healthy=True, detail=f"{n} skill(s)")


MODULE = CapabilityModule(
    id="skills",
    title="Skills",
    config_key="skills",
    description="SKILL.md packages available to the lead agent.",
    status=_status,
    operations=[
        Operation(name="skills.list", kind="read", input=Empty, output=Items, handler=_list, description="Every installed skill, its category and whether it is enabled."),
        Operation(name="skills.toggle", kind="write", input=ToggleIn, output=Ok, handler=_toggle, description="Enable or disable a skill.", admin_only=True),
    ],
)
