"""The capability registry: one declaration per feature, deterministic
derivations (snapshot, harness tools, MCP listing) from it.

Every surface Nova exposes — settings page, harness tool, MCP tool, generic
invoke endpoint — is derived from ``CapabilityRegistry.snapshot()``. These
tests pin the properties the derivations rely on: names are unique and
sorted, the snapshot is stable across registration order, kinds gate what
becomes a harness tool, and invoke validates input against the declared
model and enforces admin-only ops.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from deerflow.capabilities import (
    CapabilityModule,
    CapabilityRegistry,
    ModuleStatus,
    OpContext,
    Operation,
    OperationDenied,
    OperationNotFound,
)


class EchoIn(BaseModel):
    text: str = Field(description="What to echo")
    times: int = Field(default=1, ge=1, le=3)


class EchoOut(BaseModel):
    text: str


async def _echo(ctx: OpContext, inp: EchoIn) -> EchoOut:
    return EchoOut(text=inp.text * inp.times)


class WhoIn(BaseModel):
    pass


class WhoOut(BaseModel):
    user_id: str
    is_admin: bool


async def _who(ctx: OpContext, inp: WhoIn) -> WhoOut:
    return WhoOut(user_id=ctx.user_id, is_admin=ctx.is_admin)


async def _status() -> ModuleStatus:
    return ModuleStatus(configured=True, healthy=True, detail="ok")


def _module(id: str = "echo", *, flag: str | None = None, extra_ops: list[Operation] | None = None) -> CapabilityModule:
    ops = [
        Operation(name=f"{id}.say", kind="read", input=EchoIn, output=EchoOut, handler=_echo, description="Echo text"),
        Operation(name=f"{id}.whoami", kind="read", input=WhoIn, output=WhoOut, handler=_who, description="Caller identity"),
    ] + (extra_ops or [])
    return CapabilityModule(id=id, title=id.title(), flag=flag, operations=ops, status=_status)


def _ctx(*, user_id: str = "u1", is_admin: bool = False) -> OpContext:
    return OpContext(user_id=user_id, is_admin=is_admin, thread_id=None)


def test_snapshot_is_sorted_and_registration_order_independent():
    a = CapabilityRegistry()
    a.register(_module("zeta"))
    a.register(_module("alpha"))
    b = CapabilityRegistry()
    b.register(_module("alpha"))
    b.register(_module("zeta"))
    assert a.snapshot() == b.snapshot()
    names = [op["name"] for op in a.snapshot()["operations"]]
    assert names == sorted(names)
    assert [m["id"] for m in a.snapshot()["modules"]] == ["alpha", "zeta"]


def test_snapshot_carries_json_schemas_and_kind():
    reg = CapabilityRegistry()
    reg.register(_module())
    say = next(op for op in reg.snapshot()["operations"] if op["name"] == "echo.say")
    assert say["kind"] == "read"
    assert say["module"] == "echo"
    assert say["input_schema"]["properties"]["text"]["description"] == "What to echo"
    assert say["output_schema"]["properties"]["text"]["type"] == "string"
    assert say["harness"] is True and say["mcp"] is True


def test_operation_names_must_be_unique_and_prefixed_by_module():
    reg = CapabilityRegistry()
    reg.register(_module())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_module())
    with pytest.raises(ValueError, match="must start with"):
        bad = CapabilityModule(
            id="other",
            title="Other",
            operations=[Operation(name="echo.stray", kind="read", input=EchoIn, output=EchoOut, handler=_echo, description="x")],
            status=_status,
        )
        reg.register(bad)


@pytest.mark.asyncio
async def test_invoke_validates_input_and_returns_output_model():
    reg = CapabilityRegistry()
    reg.register(_module())
    out = await reg.invoke("echo.say", _ctx(), {"text": "ab", "times": 2})
    assert out == {"text": "abab"}
    with pytest.raises(ValueError):
        await reg.invoke("echo.say", _ctx(), {"text": "ab", "times": 99})
    with pytest.raises(OperationNotFound):
        await reg.invoke("echo.nope", _ctx(), {})


@pytest.mark.asyncio
async def test_invoke_passes_context_and_enforces_admin_only():
    async def _secret(ctx: OpContext, inp: WhoIn) -> WhoOut:
        return WhoOut(user_id=ctx.user_id, is_admin=ctx.is_admin)

    admin_op = Operation(name="echo.admin", kind="admin", input=WhoIn, output=WhoOut, handler=_secret, description="admin only", admin_only=True)
    reg = CapabilityRegistry()
    reg.register(_module(extra_ops=[admin_op]))
    assert await reg.invoke("echo.whoami", _ctx(user_id="u9"), {}) == {"user_id": "u9", "is_admin": False}
    with pytest.raises(OperationDenied):
        await reg.invoke("echo.admin", _ctx(), {})
    assert (await reg.invoke("echo.admin", _ctx(is_admin=True), {}))["is_admin"] is True


def test_flag_gating_hides_ops_when_flag_off():
    reg = CapabilityRegistry()
    reg.register(_module("gated", flag="email_marketing"))
    reg.register(_module("open"))
    on = {op.name for op in reg.operations(enabled_flags={"email_marketing"})}
    off = {op.name for op in reg.operations(enabled_flags=set())}
    assert "gated.say" in on and "gated.say" not in off
    assert "open.say" in on and "open.say" in off
    # The snapshot is the contract: it lists everything regardless of flags.
    assert {op["name"] for op in reg.snapshot()["operations"]} >= on


@pytest.mark.asyncio
async def test_module_status_is_collected_per_module():
    reg = CapabilityRegistry()
    reg.register(_module())
    statuses = await reg.statuses()
    assert statuses["echo"].configured is True
    assert statuses["echo"].detail == "ok"


def test_harness_tools_are_derived_only_for_harness_ops_and_allowed_kinds():
    from deerflow.tools.capability_tools import build_capability_tools

    async def _h(ctx: OpContext, inp: EchoIn) -> EchoOut:
        return EchoOut(text=inp.text)

    secret_op = Operation(name="echo.secret", kind="secret", input=EchoIn, output=EchoOut, handler=_h, description="s")
    hidden_op = Operation(name="echo.hidden", kind="read", input=EchoIn, output=EchoOut, handler=_h, description="h", harness=False)
    reg = CapabilityRegistry()
    reg.register(_module(extra_ops=[secret_op, hidden_op]))
    tools = build_capability_tools(reg, enabled_flags=set(), groups={"nova:echo"})
    names = {t.name for t in tools}
    assert names == {"echo__say", "echo__whoami"}
    assert build_capability_tools(reg, enabled_flags=set(), groups=set()) == []
    say = next(t for t in tools if t.name == "echo__say")
    assert say.args_schema.model_json_schema()["properties"]["text"]["description"] == "What to echo"


@pytest.mark.asyncio
async def test_harness_tool_invocation_uses_effective_user_context(monkeypatch):
    from deerflow.tools.capability_tools import build_capability_tools

    monkeypatch.setattr("deerflow.tools.capability_tools.get_effective_user_id", lambda: "harness-user")
    reg = CapabilityRegistry()
    reg.register(_module())
    who = next(t for t in build_capability_tools(reg, enabled_flags=set(), groups={"nova:echo"}) if t.name == "echo__whoami")
    result = await who.ainvoke({})
    assert '"user_id": "harness-user"' in result


def test_get_available_tools_includes_capability_tools_only_for_configured_groups(monkeypatch):
    """`tool_groups: [{name: nova:jobs}]` in config.yaml turns the jobs
    operations into lead-agent tools; without it nothing leaks in."""
    from deerflow.config.app_config import get_app_config
    from deerflow.config.tool_config import ToolGroupConfig
    from deerflow.tools.tools import get_available_tools

    base = get_app_config()
    without = base.model_copy(update={"tool_groups": [g for g in base.tool_groups if not g.name.startswith("nova:")], "tools": []})
    names = {t.name for t in get_available_tools(app_config=without, include_mcp=False)}
    assert not any(n.startswith("jobs__") for n in names)

    with_jobs = without.model_copy(update={"tool_groups": [*without.tool_groups, ToolGroupConfig(name="nova:jobs"), ToolGroupConfig(name="nova:models")]})
    names = {t.name for t in get_available_tools(app_config=with_jobs, include_mcp=False)}
    assert "models__list" in names and "models__probe" in names
    # jobs is flag-gated on jobs.enabled; either way nothing admin-only or secret appears
    assert "jobs__summary" not in names and not any(n.startswith("secrets__") for n in names)
    # an agent that only allows `web` does not get capability tools even when configured
    narrowed = {t.name for t in get_available_tools(app_config=with_jobs, include_mcp=False, groups=["web"])}
    assert not any(n.startswith("models__") for n in narrowed)


def test_feature_flag_keys_are_pinned_on_both_sides():
    """`compute_flags()` (the source), `FeatureFlags` (the API model) and the
    frontend `FeatureFlagKey` union must list the same keys."""
    import re
    from pathlib import Path

    from app.gateway.routers.capabilities import FeatureFlags
    from deerflow.capabilities.modules.features import FLAG_KEYS

    assert set(FeatureFlags.model_fields) == set(FLAG_KEYS)
    types_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "types.ts").read_text(encoding="utf-8")
    block = re.search(r"export type FeatureFlagKey\s*=\s*(.*?);", types_ts, re.S).group(1)
    assert set(re.findall(r'"([a-z_]+)"', block)) == set(FLAG_KEYS)


def test_builtin_registry_snapshot_matches_contract():
    """`contracts/capabilities.baseline.json` is the pinned contract: an
    operation may be added, never removed or retyped without refreshing the
    baseline in the same commit (see scripts/capabilities_snapshot.py)."""
    import json
    from pathlib import Path

    from app.gateway.capabilities_modules import register_gateway_modules
    from deerflow.capabilities import CapabilityRegistry
    from deerflow.capabilities.modules import register_builtin_modules

    reg = CapabilityRegistry()
    register_builtin_modules(reg)
    register_gateway_modules(reg)
    live = {op["name"]: op for op in reg.snapshot()["operations"]}
    baseline_path = Path(__file__).resolve().parents[2] / "contracts" / "capabilities.baseline.json"
    baseline = {op["name"]: op for op in json.loads(baseline_path.read_text(encoding="utf-8"))["operations"]}
    removed = sorted(set(baseline) - set(live))
    assert not removed, f"operations removed from the registry: {removed}"
    changed = sorted(n for n in baseline if baseline[n] != live[n])
    assert not changed, f"operations changed since the baseline (refresh it in this commit): {changed}"
