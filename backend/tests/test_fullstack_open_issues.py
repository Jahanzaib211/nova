"""Fixes for four defects found auditing concurrent agent work on 2026-09-21.

Each had the same shape: the change looked right and did nothing.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. require_auth must actually wrap the registered endpoint.
# ---------------------------------------------------------------------------


def test_assistants_endpoints_are_really_wrapped_by_require_auth():
    """Decorator order is load-bearing.

    With ``@require_auth`` ABOVE ``@router.post``, FastAPI registers the raw
    function and the auth wrapper is discarded — the endpoint looks protected
    in the source and is not. ``@router`` must be outermost.
    """
    from app.gateway.routers.assistants_compat import router

    paths = {"/api/assistants/search", "/api/assistants/{assistant_id}"}
    checked = 0
    for route in router.routes:
        if getattr(route, "path", None) not in paths:
            continue
        checked += 1
        endpoint = route.endpoint
        assert getattr(endpoint, "__wrapped__", None) is not None, f"{route.path} endpoint is not wrapped — @require_auth is above @router and does nothing"
    assert checked, "no assistants routes found"


def test_assistants_endpoints_accept_a_request_parameter():
    """require_auth resolves the request from kwargs and raises without it.

    Fixing the decorator order alone would have turned all four endpoints
    into 500s.
    """
    import inspect

    from app.gateway.routers import assistants_compat as mod

    for name in ("search_assistants", "get_assistant_compat", "get_assistant_graph", "get_assistant_schemas"):
        fn = getattr(mod, name)
        target = getattr(fn, "__wrapped__", fn)
        assert "request" in inspect.signature(target).parameters, f"{name} takes no request"


# ---------------------------------------------------------------------------
# 2. Gateway config must honour its documented reload.
# ---------------------------------------------------------------------------


def test_gateway_config_rebuilds_when_env_changes(monkeypatch):
    from app.gateway import config as gwconfig

    monkeypatch.setattr(gwconfig, "_gateway_config", None)
    monkeypatch.setattr(gwconfig, "_gateway_config_env", None)

    monkeypatch.setenv("GATEWAY_ENABLE_DOCS", "true")
    assert gwconfig.get_gateway_config().enable_docs is True

    monkeypatch.setenv("GATEWAY_ENABLE_DOCS", "false")
    assert gwconfig.get_gateway_config().enable_docs is False, "cached config ignored the env change"


def test_gateway_config_is_cached_when_env_is_unchanged(monkeypatch):
    from app.gateway import config as gwconfig

    monkeypatch.setattr(gwconfig, "_gateway_config", None)
    monkeypatch.setattr(gwconfig, "_gateway_config_env", None)
    monkeypatch.setenv("GATEWAY_ENABLE_DOCS", "true")

    assert gwconfig.get_gateway_config() is gwconfig.get_gateway_config()


# ---------------------------------------------------------------------------
# 3. The ACP gate must read the key the agents actually live under.
# ---------------------------------------------------------------------------

ACP_GATE = REPO / "scripts" / "gates" / "acp-gate.py"
_spec = importlib.util.spec_from_file_location("acp_gate", ACP_GATE)
assert _spec and _spec.loader
acp_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acp_gate)


def test_acp_gate_reads_acp_agents_not_runtimes():
    """`runtimes:` and `acp_agents:` are both mappings; only one has binaries.

    Reading `runtimes` as a list always yielded [], so the gate reported
    "No runtimes configured" with two working agents present.
    """
    config = {
        "runtimes": {"enabled": True, "default": "native"},
        "acp_agents": {
            "claude_code": {"command": "npx", "args": ["-y", "pkg"]},
            "openclaw": {"command": "node", "args": ["x.mjs"]},
        },
    }
    found = acp_gate.get_runtimes(config)
    assert {r["name"] for r in found} == {"claude_code", "openclaw"}
    assert {r["binary"] for r in found} == {"npx", "node"}


def test_acp_gate_tolerates_missing_or_odd_config():
    assert acp_gate.get_runtimes({}) == []
    assert acp_gate.get_runtimes({"acp_agents": None}) == []
    assert acp_gate.get_runtimes({"acp_agents": ["not", "a", "mapping"]}) == []
    assert acp_gate.get_runtimes({"acp_agents": {"x": "not-a-dict"}}) == []


def test_acp_gate_matches_the_live_config():
    """The real config.yaml must yield the agents the runtime registry reports."""
    import yaml

    config = yaml.safe_load((REPO / "config.yaml").read_text())
    names = {r["name"] for r in acp_gate.get_runtimes(config)}
    assert "claude_code" in names, f"live config yielded {names}"


# ---------------------------------------------------------------------------
# 4. The background sweep must never fail a launch.
# ---------------------------------------------------------------------------


def test_sweep_evicts_old_terminal_tasks():
    from deerflow.subagents import executor as ex

    old = ex.SubagentResult(task_id="old", trace_id="t", status=ex.SubagentStatus.COMPLETED)
    old.completed_at = datetime.now(UTC) - timedelta(seconds=ex._BACKGROUND_TASK_TTL_SEC + 60)
    fresh = ex.SubagentResult(task_id="new", trace_id="t", status=ex.SubagentStatus.COMPLETED)
    fresh.completed_at = datetime.now(UTC)

    with ex._background_tasks_lock:
        ex._background_tasks.clear()
        ex._background_tasks.update({"old": old, "new": fresh})
    try:
        ex._sweep_background_tasks()
        assert "old" not in ex._background_tasks
        assert "new" in ex._background_tasks
    finally:
        with ex._background_tasks_lock:
            ex._background_tasks.clear()


def test_sweep_survives_a_naive_completed_at():
    """BUG-001 territory: one naive stamp must not kill async subagents."""
    from deerflow.subagents import executor as ex

    naive = ex.SubagentResult(task_id="naive", trace_id="t", status=ex.SubagentStatus.COMPLETED)
    naive.completed_at = datetime.now()  # noqa: DTZ005 - deliberately naive

    with ex._background_tasks_lock:
        ex._background_tasks.clear()
        ex._background_tasks["naive"] = naive
    try:
        ex._sweep_background_tasks()  # must not raise
    finally:
        with ex._background_tasks_lock:
            ex._background_tasks.clear()


def test_sweep_never_raises_on_a_hostile_entry():
    from deerflow.subagents import executor as ex

    class Hostile:
        status = None

        @property
        def completed_at(self):
            raise RuntimeError("boom")

    with ex._background_tasks_lock:
        ex._background_tasks.clear()
        ex._background_tasks["bad"] = Hostile()
    try:
        ex._sweep_background_tasks()  # must not raise
    finally:
        with ex._background_tasks_lock:
            ex._background_tasks.clear()
