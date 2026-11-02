"""GET /api/agents/registry must reach the registry, not /api/agents/{name}.

Both routers share the /api/agents prefix. When the agents router was mounted
first, its ``/agents/{name}`` route matched ``registry`` as an agent name and
answered 404, so the Agents page's registry never loaded in production while
every unit test of the registry itself passed.
"""

from fastapi.routing import iter_route_contexts


def _get_paths(app) -> list[str]:
    # FastAPI >= 0.16x keeps each include_router() as one lazy entry in
    # app.routes; iter_route_contexts flattens them in dispatch order.
    return [r.path for r in iter_route_contexts(app.routes) if r.methods and "GET" in r.methods]


def test_registry_route_precedes_agent_by_name():
    from app.gateway.app import create_app

    paths = _get_paths(create_app())
    assert "/api/agents/registry" in paths
    assert "/api/agents/{name}" in paths
    assert paths.index("/api/agents/registry") < paths.index("/api/agents/{name}")
