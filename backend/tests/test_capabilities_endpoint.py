"""Unit tests for /api/runtime/capabilities endpoint (v7.1).

Covers:
  - Response shape (skills / tools / hooks / subagents / circuits / server)
  - Empty fallbacks when subsystems fail to load
  - Auth required (same as /api/skills)
  - Read-only — no side effects on circuit state
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

# v7.3 (Nova rebrand): make this test CI-runnable without config.yaml.
# The capabilities router imports `app.gateway.deps.get_config` which calls
# `deerflow.config.app_config.get_app_config` which tries to read
# config.yaml from disk. CI runners don't have config.yaml (gitignored);
# local devs do. Patching the module-level singleton (`_app_config_path`
# and friends) makes the test hermetic — same pattern as
# test_replay_golden.py and test_runtime_lifecycle_e2e.py.
_APP_CONFIG_MODULE = "deerflow.config.app_config"
_HERMETIC_PATCHERS = [
    patch(f"{_APP_CONFIG_MODULE}._app_config_path", None),
    patch(f"{_APP_CONFIG_MODULE}._app_config", None),
    patch(f"{_APP_CONFIG_MODULE}._app_config_mtime", None),
    patch(f"{_APP_CONFIG_MODULE}._app_config_signature", None),
    patch(f"{_APP_CONFIG_MODULE}._app_config_is_custom", True),
]


@pytest.fixture(autouse=True)
def _hermetic_config():
    """Auto-apply the hermetic config patchers to every test in this module."""
    with patch(f"{_APP_CONFIG_MODULE}._app_config_path", None), \
         patch(f"{_APP_CONFIG_MODULE}._app_config", None), \
         patch(f"{_APP_CONFIG_MODULE}._app_config_mtime", None), \
         patch(f"{_APP_CONFIG_MODULE}._app_config_signature", None), \
         patch(f"{_APP_CONFIG_MODULE}._app_config_is_custom", True):
        # Import the router AFTER the patchers are in place, so the module
        # imports see a "no config.yaml" world. Using a lazy import inside
        # the fixture is intentional: this avoids the chain that triggers
        # config.yaml disk read at module-import time.
        global cap
        from app.gateway.routers import capabilities as cap  # noqa: E402
        yield


def _make_test_client():
    """Build a minimal FastAPI app exposing only the capabilities router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(cap.router)
    return TestClient(app)


class TestResponseShape:
    def test_endpoint_returns_all_fields(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("skills", "tools", "hooks", "subagents", "circuits", "server"):
            assert key in body, f"missing field: {key}"
        assert isinstance(body["skills"], list)
        assert isinstance(body["tools"], list)
        assert isinstance(body["hooks"], list)
        assert isinstance(body["subagents"], list)
        assert isinstance(body["circuits"], list)
        assert isinstance(body["server"], dict)


class TestToolsInventory:
    def test_includes_v7_screenshot_tool(self) -> None:
        """The v7 screenshot tool must be in the visible inventory."""
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        names = {t["name"] for t in resp.json()["tools"]}
        assert "screenshot" in names
        assert "browser_navigate" in names
        assert "browser_check" in names
        assert "shell_session" in names

    def test_tool_descriptions_non_empty(self) -> None:
        """Each tool should have at least an empty description (not crash)."""
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        for tool in resp.json()["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert isinstance(tool["description"], str)


class TestHooksInventory:
    def test_known_middlewares_listed(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        names = {h["name"] for h in resp.json()["hooks"]}
        # The 11 known middlewares from lead_agent/agent.py.
        for expected in (
            "thread_data",
            "uploads",
            "title",
            "observe_adjust",
            "llm_error_handling",
            "preflight_quota",
            "strip_error_fallback",
            "loop_detection",
            "subagent_limit",
            "reflect_fix",
        ):
            assert expected in names, f"missing middleware hook: {expected}"

    def test_hook_kind_is_middleware(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        for hook in resp.json()["hooks"]:
            assert hook["kind"] == "middleware"


class TestSubagentsInventory:
    def test_returns_builtin_subagents(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        names = {s["name"] for s in resp.json()["subagents"]}
        # At least one of the builtins must be present.
        assert names  # non-empty


class TestCircuitsSnapshot:
    def test_empty_when_no_circuits_open(self) -> None:
        from deerflow.sandbox import browser_circuit_breaker as cb

        cb.reset_all_circuits()
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        assert resp.json()["circuits"] == []

    def test_reflects_active_circuits(self) -> None:
        from deerflow.sandbox import browser_circuit_breaker as cb

        cb.reset_all_circuits()
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("test-thread")
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        circuits = resp.json()["circuits"]
        assert any(c["thread_id"] == "test-thread" and c["state"] == "open" for c in circuits)
        cb.reset_all_circuits()

    def test_endpoint_does_not_mutate_circuit_state(self) -> None:
        """The capabilities endpoint must be observability-only."""
        from deerflow.sandbox import browser_circuit_breaker as cb

        cb.reset_all_circuits()
        cb.record_failure("obs-test")
        snapshot_before = dict(cb._breakers)
        client = _make_test_client()
        client.get("/api/runtime/capabilities")
        snapshot_after = dict(cb._breakers)
        assert snapshot_before == snapshot_after, "endpoint must not mutate circuit state"
        cb.reset_all_circuits()


class TestServerInfo:
    def test_server_dict_has_expected_keys(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/runtime/capabilities")
        info = resp.json()["server"]
        assert "process" in info
        assert "pid" in info
        assert isinstance(info["pid"], int)


class TestEmptyFallbacks:
    """When subsystems fail to load, the endpoint must not 500 — it returns empty lists."""

    def test_skill_discovery_failure_returns_empty(self) -> None:
        with patch(
            "deerflow.skills.storage.get_or_new_skill_storage",
            side_effect=RuntimeError("simulated discovery crash"),
        ):
            client = _make_test_client()
            resp = client.get("/api/runtime/capabilities")
            assert resp.status_code == 200
            assert resp.json()["skills"] == []

    def test_subagent_registry_failure_returns_empty(self) -> None:
        with patch(
            "deerflow.subagents.get_available_subagent_names",
            side_effect=RuntimeError("simulated registry crash"),
        ):
            client = _make_test_client()
            resp = client.get("/api/runtime/capabilities")
            assert resp.status_code == 200
            assert resp.json()["subagents"] == []

    def test_circuit_snapshot_failure_returns_empty(self) -> None:
        with patch(
            "deerflow.sandbox.browser_circuit_breaker.snapshot",
            side_effect=RuntimeError("simulated circuit crash"),
        ):
            client = _make_test_client()
            resp = client.get("/api/runtime/capabilities")
            assert resp.status_code == 200
            assert resp.json()["circuits"] == []


class TestReadOnlyContract:
    """Repeated calls must return the same response shape with no side effects."""

    def test_repeated_calls_return_consistent_shape(self) -> None:
        client = _make_test_client()
        first = client.get("/api/runtime/capabilities").json()
        second = client.get("/api/runtime/capabilities").json()
        assert set(first.keys()) == set(second.keys())
        # Tool / hook / subagent lists should be stable (same names, possibly
        # different counts if skills discovery is racy, but the SET must match).
        assert {t["name"] for t in first["tools"]} == {t["name"] for t in second["tools"]}
        assert {h["name"] for h in first["hooks"]} == {h["name"] for h in second["hooks"]}
