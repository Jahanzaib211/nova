"""Unit tests for the Nova typed service layer.

Phase C1 — verifies delegation, typing, Protocol compliance, and
constructor injection for all 10 services.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.services.protocols import (
    ArtifactService,
    BrowserService,
    ConfigurationService,
    DiagnosticsService,
    HealthService,
    RecoveryService,
    RepositoryService,
    RunService,
    TerminalService,
    WorkspaceService,
)
from deerflow.services.types import (
    DiagnosticsRecord,
    HealthReport,
    ProbeResult,
    RecoveryAction,
    RunDetail,
    RunSummary,
    WorkspacePaths,
)

# =====================================================================
# Protocol compliance — verify concrete implementations satisfy protocols
# =====================================================================


class TestProtocolCompliance:
    """Verify that each implementation has the expected protocol methods."""

    def test_run_service_impl_has_methods(self):
        from deerflow.services.implementations import RunServiceImpl

        mock_manager = MagicMock()
        impl = RunServiceImpl(mock_manager)
        assert hasattr(impl, "create")
        assert hasattr(impl, "get")
        assert hasattr(impl, "list_by_thread")
        assert hasattr(impl, "cancel")
        assert hasattr(impl, "set_status")
        assert asyncio.iscoroutinefunction(impl.create)
        assert asyncio.iscoroutinefunction(impl.get)

    def test_workspace_service_impl_has_methods(self):
        from deerflow.services.implementations import WorkspaceServiceImpl

        impl = WorkspaceServiceImpl()
        assert hasattr(impl, "resolve_paths")
        assert hasattr(impl, "ensure_directories")

    def test_repository_service_impl_has_methods(self):
        from deerflow.services.implementations import RepositoryServiceImpl

        mock_store = MagicMock()
        impl = RepositoryServiceImpl(mock_store)
        assert hasattr(impl, "put")
        assert hasattr(impl, "get")
        assert hasattr(impl, "list_by_thread")
        assert asyncio.iscoroutinefunction(impl.put)

    def test_browser_service_impl_has_methods(self):
        from deerflow.services.implementations import BrowserServiceImpl

        impl = BrowserServiceImpl()
        assert hasattr(impl, "check")
        assert hasattr(impl, "is_circuit_open")
        assert asyncio.iscoroutinefunction(impl.check)

    def test_terminal_service_impl_has_methods(self):
        from deerflow.services.implementations import TerminalServiceImpl

        impl = TerminalServiceImpl()
        assert hasattr(impl, "execute")
        assert asyncio.iscoroutinefunction(impl.execute)

    def test_artifact_service_impl_has_methods(self):
        from deerflow.services.implementations import ArtifactServiceImpl

        impl = ArtifactServiceImpl()
        assert hasattr(impl, "get")
        assert hasattr(impl, "list")
        assert asyncio.iscoroutinefunction(impl.get)

    def test_health_service_impl_has_methods(self):
        from deerflow.services.implementations import HealthServiceImpl

        impl = HealthServiceImpl()
        assert hasattr(impl, "check_all")
        assert hasattr(impl, "check_single")
        assert asyncio.iscoroutinefunction(impl.check_all)

    def test_recovery_service_impl_has_methods(self):
        from deerflow.services.implementations import RecoveryServiceImpl

        impl = RecoveryServiceImpl()
        assert hasattr(impl, "recover")
        assert asyncio.iscoroutinefunction(impl.recover)

    def test_configuration_service_impl_has_methods(self):
        from deerflow.services.implementations import ConfigurationServiceImpl

        impl = ConfigurationServiceImpl()
        assert hasattr(impl, "get_config")
        assert hasattr(impl, "get_config_value")

    def test_diagnostics_service_impl_has_methods(self):
        from deerflow.services.implementations import DiagnosticsServiceImpl

        impl = DiagnosticsServiceImpl()
        assert hasattr(impl, "record")
        assert hasattr(impl, "read_recent")
        assert hasattr(impl, "register_correlation_id")
        assert hasattr(impl, "lookup_correlation_id")


# =====================================================================
# Type dataclass tests
# =====================================================================


class TestTypes:
    def test_run_summary_frozen(self):
        s = RunSummary(run_id="r1", thread_id="t1", status="running")
        assert s.run_id == "r1"
        assert s.correlation_id == ""
        with pytest.raises(AttributeError):
            s.run_id = "r2"  # type: ignore[misc]

    def test_run_detail_defaults(self):
        d = RunDetail(run_id="r1", thread_id="t1", assistant_id="lead-agent", status="pending")
        assert d.total_tokens == 0
        assert d.metadata == {}

    def test_probe_result(self):
        p = ProbeResult(name="nginx", healthy=True, latency_ms=12.5)
        assert p.healthy is True

    def test_health_report(self):
        h = HealthReport(healthy=True, probes=[], probe_count=0, healthy_count=0)
        assert h.healthy is True

    def test_recovery_action(self):
        r = RecoveryAction(component="tunnel", action="restart", success=True)
        assert r.success is True

    def test_diagnostics_record(self):
        dr = DiagnosticsRecord(seq=1, stage="test")
        assert dr.correlation_id == ""

    def test_workspace_paths(self):
        wp = WorkspacePaths(workspace="/tmp/ws", uploads="/tmp/up", outputs="/tmp/out")
        assert wp.workspace == "/tmp/ws"


# =====================================================================
# RunServiceImpl delegation tests
# =====================================================================


class TestRunServiceImpl:
    def test_create_delegates_to_manager(self):
        from deerflow.services.implementations import RunServiceImpl

        mock_manager = MagicMock()
        mock_record = MagicMock()
        mock_record.run_id = "r1"
        mock_record.thread_id = "t1"
        mock_record.assistant_id = "lead-agent"
        mock_record.status = "running"
        mock_record.correlation_id = "corr1"
        mock_record.model_name = "gpt-4"
        mock_record.created_at = "2026-07-12T00:00:00"
        mock_record.error = None
        mock_record.total_tokens = 100
        mock_record.message_count = 5
        mock_manager.create = AsyncMock(return_value=mock_record)

        impl = RunServiceImpl(mock_manager)
        result = asyncio.run(
            impl.create("t1", model_name="gpt-4")
        )
        assert result.run_id == "r1"
        assert result.status == "running"
        mock_manager.create.assert_called_once()

    def test_get_returns_none_when_not_found(self):
        from deerflow.services.implementations import RunServiceImpl

        mock_manager = MagicMock()
        mock_manager.get = AsyncMock(return_value=None)

        impl = RunServiceImpl(mock_manager)
        result = asyncio.run(impl.get("nonexistent"))
        assert result is None

    def test_cancel_delegates(self):
        from deerflow.services.implementations import RunServiceImpl

        mock_manager = MagicMock()
        mock_manager.cancel = AsyncMock(return_value=True)

        impl = RunServiceImpl(mock_manager)
        result = asyncio.run(impl.cancel("r1"))
        assert result is True

    def test_create_or_reject_delegates(self):
        from deerflow.services.implementations import RunServiceImpl

        mock_manager = MagicMock()
        mock_record = MagicMock()
        mock_record.run_id = "r2"
        mock_record.thread_id = "t1"
        mock_record.assistant_id = "lead-agent"
        mock_record.status = "pending"
        mock_record.correlation_id = "corr2"
        mock_record.model_name = "gpt-4"
        mock_record.created_at = "2026-07-12T00:00:00"
        mock_record.error = None
        mock_record.total_tokens = 0
        mock_record.message_count = 0
        mock_manager.create_or_reject = AsyncMock(return_value=mock_record)

        impl = RunServiceImpl(mock_manager)
        result = asyncio.run(
            impl.create_or_reject("t1", model_name="gpt-4", multitask_strategy="reject")
        )
        assert result.run_id == "r2"
        assert result.status == "pending"
        mock_manager.create_or_reject.assert_called_once()


# =====================================================================
# WorkspaceServiceImpl tests
# =====================================================================


class TestWorkspaceServiceImpl:
    def test_resolve_paths_default_user(self):
        from deerflow.services.implementations import WorkspaceServiceImpl

        impl = WorkspaceServiceImpl(base_dir=".deer-flow")
        paths = impl.resolve_paths("thread-123")
        assert "thread-123" in paths.workspace
        assert "default" in paths.workspace

    def test_resolve_paths_custom_user(self):
        from deerflow.services.implementations import WorkspaceServiceImpl

        impl = WorkspaceServiceImpl(base_dir=".deer-flow")
        paths = impl.resolve_paths("thread-123", user_id="alice")
        assert "alice" in paths.workspace

    def test_ensure_directories_creates_dirs(self, tmp_path):
        from deerflow.services.implementations import WorkspaceServiceImpl

        impl = WorkspaceServiceImpl(base_dir=str(tmp_path / "df"))
        paths = impl.ensure_directories("t1")
        import os
        assert os.path.isdir(paths.workspace)
        assert os.path.isdir(paths.uploads)
        assert os.path.isdir(paths.outputs)


# =====================================================================
# RepositoryServiceImpl tests
# =====================================================================


class TestRepositoryServiceImpl:
    def test_put_delegates_to_store(self):
        from deerflow.services.implementations import RepositoryServiceImpl

        mock_store = MagicMock()
        mock_store.put = AsyncMock()

        impl = RepositoryServiceImpl(mock_store)
        asyncio.run(
            impl.put("r1", thread_id="t1", status="running")
        )
        mock_store.put.assert_called_once()

    def test_get_delegates(self):
        from deerflow.services.implementations import RepositoryServiceImpl

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value={"run_id": "r1"})

        impl = RepositoryServiceImpl(mock_store)
        result = asyncio.run(impl.get("r1"))
        assert result == {"run_id": "r1"}


# =====================================================================
# BrowserServiceImpl tests
# =====================================================================


class TestBrowserServiceImpl:
    def test_circuit_starts_closed(self):
        from deerflow.services.implementations import BrowserServiceImpl

        impl = BrowserServiceImpl()
        assert impl.is_circuit_open() is False

    def test_circuit_opens_after_failures(self):
        from deerflow.services.implementations import BrowserServiceImpl

        impl = BrowserServiceImpl()
        impl._failure_count = 3
        impl._circuit_open = True
        assert impl.is_circuit_open() is True


# =====================================================================
# HealthServiceImpl tests
# =====================================================================


class TestHealthServiceImpl:
    def test_check_all_with_no_probes(self):
        from deerflow.services.implementations import HealthServiceImpl

        impl = HealthServiceImpl(probes={})
        report = asyncio.run(impl.check_all())
        assert report.healthy is True
        assert report.probe_count == 0

    def test_check_single_unknown_probe(self):
        from deerflow.services.implementations import HealthServiceImpl

        impl = HealthServiceImpl(probes={})
        result = asyncio.run(impl.check_single("nonexistent"))
        assert result.healthy is False
        assert "Unknown" in result.message


# =====================================================================
# ConfigurationServiceImpl tests
# =====================================================================


class TestConfigurationServiceImpl:
    def test_get_config_value_missing_key_returns_default(self):
        from deerflow.services.implementations import ConfigurationServiceImpl

        impl = ConfigurationServiceImpl()
        mock_config = MagicMock()
        # Make getattr raise AttributeError for the nested path
        del mock_config.nonexistent
        with patch("deerflow.config.get_app_config", return_value=mock_config):
            result = impl.get_config_value("nonexistent.key", default="fallback")
            assert result == "fallback"

    def test_get_config_value_existing_key(self):
        from deerflow.services.implementations import ConfigurationServiceImpl

        impl = ConfigurationServiceImpl()
        mock_config = MagicMock()
        mock_config.models = [{"name": "test"}]
        with patch("deerflow.config.get_app_config", return_value=mock_config):
            result = impl.get_config_value("models")
            assert result == [{"name": "test"}]


# =====================================================================
# Package __init__ exports
# =====================================================================


class TestPackageExports:
    def test_all_protocols_exported(self):
        from deerflow.services import (
            ArtifactService,
            BrowserService,
            ConfigurationService,
            DiagnosticsService,
            HealthService,
            RecoveryService,
            RepositoryService,
            RunService,
            TerminalService,
            WorkspaceService,
        )

        assert all(cls is not None for cls in [
            RunService, WorkspaceService, RepositoryService, BrowserService,
            TerminalService, ArtifactService, HealthService, RecoveryService,
            ConfigurationService, DiagnosticsService,
        ])

    def test_all_types_exported(self):
        from deerflow.services import (
            DiagnosticsRecord,
            HealthReport,
            ProbeResult,
            RecoveryAction,
            RunDetail,
            RunSummary,
            WorkspacePaths,
        )

        assert all(cls is not None for cls in [
            RunSummary, RunDetail, ProbeResult, HealthReport,
            RecoveryAction, DiagnosticsRecord, WorkspacePaths,
        ])
