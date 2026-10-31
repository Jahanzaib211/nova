"""Gateway-side capability modules.

These need ``app.*`` objects (the auth provider, the user model) that the
harness package may not import, so they are declared here and registered
into the same process registry at startup (``register_gateway_modules``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities import CapabilityModule, CapabilityRegistry, ModuleStatus, OpContext, Operation
from deerflow.capabilities.modules._common import Empty, Items, Ok

# ---------------------------------------------------------------- sessions


def _tokens_repo():
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.harness_token.sql import HarnessTokenRepository

    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("sessions: no database configured")
    return HarnessTokenRepository(sf)


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="What will use this token, e.g. 'claude-code on laptop'")
    scopes: list[str] = Field(default_factory=lambda: ["*"], description="Capability module ids this token may invoke, or ['*']")


class TokenCreatedOut(BaseModel):
    token: str = Field(description="Shown once. Put it in the harness's Authorization: Bearer header.")
    id: str
    name: str
    prefix: str
    scopes: list[str]
    mcp_url: str = Field(description="Where to point the harness: Nova's MCP endpoint")


class TokenIdIn(BaseModel):
    token_id: str


async def _tokens_list(ctx: OpContext, inp: Empty) -> Items:
    rows = await _tokens_repo().list(owner_user_id=ctx.user_id)
    return Items(items=rows, total=len(rows))


async def _tokens_create(ctx: OpContext, inp: TokenCreateIn) -> TokenCreatedOut:
    from app.gateway.mcp_server import MOUNT_PATH
    from deerflow.capabilities import get_registry

    known = {m.id for m in get_registry().modules()}
    bad = [s for s in inp.scopes if s != "*" and s not in known]
    if bad:
        raise ValueError(f"unknown scope(s): {', '.join(bad)}; known: {', '.join(sorted(known))}")
    created = await _tokens_repo().create(owner_user_id=ctx.user_id, name=inp.name, scopes=inp.scopes)
    return TokenCreatedOut(token=created["token"], id=created["id"], name=created["name"], prefix=created["prefix"], scopes=created["scopes"], mcp_url=MOUNT_PATH)


async def _tokens_revoke(ctx: OpContext, inp: TokenIdIn) -> Ok:
    ok = await _tokens_repo().revoke(inp.token_id, owner_user_id=ctx.user_id)
    return Ok(ok=ok, detail=None if ok else "no live token with that id belongs to you")


class SessionsOut(BaseModel):
    token_version: int
    last_sign_in_at: str | None = None
    detail: str


async def _sessions_get(ctx: OpContext, inp: Empty) -> SessionsOut:
    from app.gateway.deps import get_local_provider

    user = await get_local_provider().get_user(ctx.user_id)
    if user is None:
        raise LookupError("user not found")
    last = getattr(user, "last_sign_in_at", None)
    return SessionsOut(token_version=int(user.token_version), last_sign_in_at=last.isoformat() if last else None, detail="Browser sessions are stateless JWTs; revoking signs every device out on its next request.")


async def _sessions_revoke_all(ctx: OpContext, inp: Empty) -> Ok:
    from app.gateway.deps import get_local_provider

    provider = get_local_provider()
    user = await provider.get_user(ctx.user_id)
    if user is None:
        raise LookupError("user not found")
    user.token_version += 1
    await provider.update_user(user)
    return Ok(ok=True, detail="all browser sessions invalidated; sign in again")


async def _sessions_status() -> ModuleStatus:
    from deerflow.persistence.engine import get_session_factory

    ok = get_session_factory() is not None
    return ModuleStatus(configured=ok, healthy=ok, detail="database-backed" if ok else "no database: harness tokens unavailable")


SESSIONS = CapabilityModule(
    id="sessions",
    title="Devices & sessions",
    description="Browser sessions and harness tokens (how Claude Code / OpenClaw authenticate to Nova's MCP server).",
    status=_sessions_status,
    operations=[
        Operation(name="sessions.get", kind="read", input=Empty, output=SessionsOut, handler=_sessions_get, description="The caller's session state.", harness=False, mcp=False),
        Operation(name="sessions.revoke_all", kind="write", input=Empty, output=Ok, handler=_sessions_revoke_all, description="Sign out every device.", harness=False, mcp=False),
        Operation(name="sessions.tokens", kind="read", input=Empty, output=Items, handler=_tokens_list, description="The caller's harness tokens (never the secret).", harness=False, mcp=False),
        Operation(name="sessions.token_create", kind="secret", input=TokenCreateIn, output=TokenCreatedOut, handler=_tokens_create, description="Mint a harness token; the plaintext is returned once.", harness=False, mcp=False),
        Operation(name="sessions.token_revoke", kind="write", input=TokenIdIn, output=Ok, handler=_tokens_revoke, description="Revoke a harness token immediately.", harness=False, mcp=False),
    ],
)


# ---------------------------------------------------------------- registration


def register_gateway_modules(registry: CapabilityRegistry) -> None:
    for module in (SESSIONS,):
        if module.id not in {m.id for m in registry.modules()}:
            registry.register(module)


__all__ = ["SESSIONS", "register_gateway_modules"]
