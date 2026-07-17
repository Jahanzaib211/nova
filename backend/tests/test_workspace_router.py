"""Tests for the /api/workspace/* router (Phase C10 prerequisite).

Covers the security posture and the endpoint contract:
- feature flag (workspace.intelligence_enabled) gates every endpoint with 403
- the scanned root is derived server-side (thread workspace dir); a missing
  workspace dir is a 404
- owner_check denies cross-tenant thread access
- index → snapshot/commands/symbols/impact/metrics round-trip on a real
  temporary workspace
- the index endpoint is classified into the cost rate-limit tier
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

THREAD_ID = "thread-ws-1"


def _make_app(monkeypatch, tmp_path, *, enabled=True, owner_check_passes=True, create_dir=True):
    from app.gateway.routers import workspace as workspace_router
    from deerflow.services.implementations import WorkspaceIntelligenceServiceImpl

    workspace_dir = tmp_path / "threads" / THREAD_ID / "user-data" / "workspace"
    if create_dir:
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1'\n")
        (workspace_dir / "main.py").write_text("def hello():\n    return 'hi'\n\nclass App:\n    pass\n")

    monkeypatch.setattr(
        workspace_router,
        "get_app_config",
        lambda: SimpleNamespace(workspace=SimpleNamespace(intelligence_enabled=enabled)),
    )
    monkeypatch.setattr(
        workspace_router,
        "get_paths",
        lambda: SimpleNamespace(sandbox_work_dir=lambda thread_id, user_id=None: tmp_path / "threads" / thread_id / "user-data" / "workspace"),
    )
    service = WorkspaceIntelligenceServiceImpl()
    monkeypatch.setattr(workspace_router, "_service", lambda: service)

    app = make_authed_test_app(owner_check_passes=owner_check_passes)
    app.include_router(workspace_router.router)
    return app, service, workspace_dir


class TestFeatureFlag:
    def test_all_endpoints_403_when_disabled(self, monkeypatch, tmp_path):
        app, _, _ = _make_app(monkeypatch, tmp_path, enabled=False)
        with TestClient(app) as client:
            assert client.post(f"/api/workspace/{THREAD_ID}/index").status_code == 403
            assert client.get(f"/api/workspace/{THREAD_ID}/snapshot").status_code == 403
            assert client.post(f"/api/workspace/{THREAD_ID}/plan", json={"kind": "search", "query": "x"}).status_code == 403
            assert client.get(f"/api/workspace/{THREAD_ID}/commands").status_code == 403
            assert client.get(f"/api/workspace/{THREAD_ID}/symbols").status_code == 403
            assert client.post(f"/api/workspace/{THREAD_ID}/impact", json={"files": []}).status_code == 403
            assert client.get(f"/api/workspace/{THREAD_ID}/metrics").status_code == 403


class TestOwnership:
    def test_owner_check_denies_foreign_thread(self, monkeypatch, tmp_path):
        app, _, _ = _make_app(monkeypatch, tmp_path, owner_check_passes=False)
        with TestClient(app) as client:
            response = client.get(f"/api/workspace/{THREAD_ID}/snapshot")
        assert response.status_code in (403, 404)

    def test_missing_workspace_dir_is_404(self, monkeypatch, tmp_path):
        app, _, _ = _make_app(monkeypatch, tmp_path, create_dir=False)
        with TestClient(app) as client:
            response = client.post(f"/api/workspace/{THREAD_ID}/index")
        assert response.status_code == 404


class TestEndpointContract:
    @pytest.fixture()
    def client_and_service(self, monkeypatch, tmp_path):
        app, service, workspace_dir = _make_app(monkeypatch, tmp_path)
        with TestClient(app) as client:
            yield client, service, workspace_dir

    def test_snapshot_404_before_index(self, client_and_service):
        client, _, _ = client_and_service
        assert client.get(f"/api/workspace/{THREAD_ID}/snapshot").status_code == 404
        assert client.get(f"/api/workspace/{THREAD_ID}/commands").status_code == 404
        assert client.get(f"/api/workspace/{THREAD_ID}/symbols").status_code == 404

    def test_index_then_read_round_trip(self, client_and_service):
        client, _, _ = client_and_service

        indexed = client.post(f"/api/workspace/{THREAD_ID}/index")
        assert indexed.status_code == 200
        summary = indexed.json()["snapshot"]
        assert summary["project_count"] >= 1
        assert summary["symbol_count"] >= 2  # hello + App

        snapshot = client.get(f"/api/workspace/{THREAD_ID}/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["snapshot"]["project_count"] == summary["project_count"]

        symbols = client.get(f"/api/workspace/{THREAD_ID}/symbols", params={"q": "hel"})
        assert symbols.status_code == 200
        names = [s["name"] for s in symbols.json()["symbols"]]
        assert "hello" in names

        commands = client.get(f"/api/workspace/{THREAD_ID}/commands")
        assert commands.status_code == 200

        metrics = client.get(f"/api/workspace/{THREAD_ID}/metrics")
        assert metrics.status_code == 200
        assert "metrics" in metrics.json()

    def test_impact_reports_touched_symbols(self, client_and_service):
        client, _, workspace_dir = client_and_service
        client.post(f"/api/workspace/{THREAD_ID}/index")

        impact = client.post(
            f"/api/workspace/{THREAD_ID}/impact",
            json={"files": [str(workspace_dir / "main.py")]},
        )
        assert impact.status_code == 200
        body = impact.json()
        assert body["scanned"] is True
        assert {s["name"] for s in body["symbols"]} >= {"hello", "App"}

    def test_impact_before_index_reports_unscanned(self, client_and_service):
        client, _, _ = client_and_service
        impact = client.post(f"/api/workspace/{THREAD_ID}/impact", json={"files": ["x.py"]})
        assert impact.status_code == 200
        assert impact.json()["scanned"] is False

    def test_plan_search_returns_plan_without_executing(self, client_and_service):
        client, _, _ = client_and_service
        client.post(f"/api/workspace/{THREAD_ID}/index")
        response = client.post(
            f"/api/workspace/{THREAD_ID}/plan",
            json={"kind": "search", "query": "hello"},
        )
        assert response.status_code == 200
        assert "plan" in response.json()

    def test_plan_requires_query_or_command(self, client_and_service):
        client, _, _ = client_and_service
        assert client.post(f"/api/workspace/{THREAD_ID}/plan", json={"kind": "search"}).status_code == 422
        assert client.post(f"/api/workspace/{THREAD_ID}/plan", json={"kind": "run"}).status_code == 422


class TestRateLimitTier:
    def test_workspace_index_is_cost_tier(self):
        from app.gateway.auth_rate_limit_middleware import AuthRateLimitMiddleware

        async def _app(scope, receive, send):  # pragma: no cover
            return None

        mw = AuthRateLimitMiddleware(app=_app)
        assert mw._classify(f"/api/workspace/{THREAD_ID}/index")[0] == "cost"
        assert mw._classify(f"/api/workspace/{THREAD_ID}/snapshot") is None
