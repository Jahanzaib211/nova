"""``/api/capabilities/ops``: the generic surface the UI and typed client use.

Ownership and admin gating come from the registry; the router only turns
auth into an ``OpContext`` and registry errors into HTTP statuses.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from deerflow.capabilities import CapabilityModule, CapabilityRegistry, ModuleStatus, OpContext, Operation


class In(BaseModel):
    n: int = 1


class Out(BaseModel):
    user: str
    n: int


async def _h(ctx: OpContext, inp: In) -> Out:
    return Out(user=ctx.user_id, n=inp.n * 2)


async def _admin(ctx: OpContext, inp: In) -> Out:
    return Out(user=ctx.user_id, n=-1)


async def _status() -> ModuleStatus:
    return ModuleStatus(configured=True, healthy=False, detail="degraded")


def _registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(
        CapabilityModule(
            id="demo",
            title="Demo",
            status=_status,
            operations=[
                Operation(name="demo.double", kind="read", input=In, output=Out, handler=_h, description="double"),
                Operation(name="demo.admin", kind="admin", input=In, output=Out, handler=_admin, description="admin", admin_only=True),
            ],
        )
    )
    return reg


def _client(*, admin: bool = False) -> TestClient:
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.auth.models import User
    from app.gateway.routers import capabilities_ops

    def user():
        return User(email="ops@example.com", password_hash="x", system_role="admin" if admin else "user", id=uuid4())

    app = make_authed_test_app(user_factory=user)
    app.state.capability_registry = _registry()
    app.include_router(capabilities_ops.router)
    return TestClient(app)


def test_list_returns_snapshot_and_statuses():
    res = _client().get("/api/capabilities/ops")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot"]["version"] == 1
    assert [op["name"] for op in body["snapshot"]["operations"]] == ["demo.admin", "demo.double"]
    assert body["status"]["demo"] == {"configured": True, "healthy": False, "detail": "degraded"}
    assert body["flags"]["capabilities"] is True


def test_invoke_validates_and_returns_output():
    c = _client()
    res = c.post("/api/capabilities/ops/demo.double", json={"n": 21})
    assert res.status_code == 200, res.text
    assert res.json()["result"]["n"] == 42
    assert res.json()["result"]["user"]
    assert c.post("/api/capabilities/ops/demo.double", json={"n": "x"}).status_code == 422
    assert c.post("/api/capabilities/ops/demo.nope", json={}).status_code == 404


def test_admin_only_ops_are_403_for_users_and_200_for_admins():
    assert _client().post("/api/capabilities/ops/demo.admin", json={}).status_code == 403
    assert _client(admin=True).post("/api/capabilities/ops/demo.admin", json={}).status_code == 200


def test_handler_errors_map_to_statuses():
    async def _lookup(ctx, inp):
        raise LookupError("no such thing")

    async def _boom(ctx, inp):
        raise RuntimeError("service down")

    reg = CapabilityRegistry()
    reg.register(
        CapabilityModule(
            id="e",
            title="E",
            status=_status,
            operations=[
                Operation(name="e.lookup", kind="read", input=In, output=Out, handler=_lookup, description="l"),
                Operation(name="e.boom", kind="read", input=In, output=Out, handler=_boom, description="b"),
            ],
        )
    )
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers import capabilities_ops

    app = make_authed_test_app()
    app.state.capability_registry = reg
    app.include_router(capabilities_ops.router)
    c = TestClient(app)
    assert c.post("/api/capabilities/ops/e.lookup", json={}).status_code == 404
    res = c.post("/api/capabilities/ops/e.boom", json={})
    assert res.status_code == 502
    assert res.json()["detail"] == "RuntimeError: service down"


@pytest.mark.parametrize("path", ["/api/capabilities/ops", "/api/capabilities/ops/demo.double"])
def test_unauthenticated_is_401(path):
    from fastapi import FastAPI

    from app.gateway.routers import capabilities_ops

    app = FastAPI()
    app.state.capability_registry = _registry()
    app.include_router(capabilities_ops.router)
    c = TestClient(app)
    res = c.get(path) if path.endswith("ops") else c.post(path, json={})
    assert res.status_code == 401
