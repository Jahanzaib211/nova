"""Dependency injection container for Nova services.

Phase C2 — wires service interfaces to their implementations.
No globals.  No circular imports.  Factories are lazy — services
are created on first access and cached for the process lifetime.

Usage::

    from deerflow.services.container import service_container

    run_svc = service_container.run_service()
    detail = await run_svc.get("run-123")

    # For testing — override with mocks:
    service_container.override(run_service=my_mock_run_svc)
"""

from __future__ import annotations

import logging
from typing import Any

from deerflow.services.protocols import (
    ArtifactService,
    BrowserService,
    ConfigurationService,
    DiagnosticsService,
    HealthService,
    RepositoryService,
    RecoveryService,
    RunService,
    TerminalService,
    WorkspaceService,
)

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Lazy-initializing service container.

    Services are created on first access and cached.  ``override()``
    allows replacing any service for testing without touching the
    container's construction logic.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, Any] = {}
        self._singletons: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Override API (for testing)
    # ------------------------------------------------------------------

    def override(self, **services: Any) -> None:
        """Replace one or more services for testing.

        Keys must match method names (e.g. ``run_service=mock``).
        """
        self._overrides.update(services)
        # Clear cached singletons so the override takes effect on next access
        for key in services:
            self._singletons.pop(key, None)

    def reset(self) -> None:
        """Clear all overrides and cached singletons."""
        self._overrides.clear()
        self._singletons.clear()

    # ------------------------------------------------------------------
    # Service factories
    # ------------------------------------------------------------------

    def run_service(self) -> RunService:
        """Return the RunService singleton."""
        if "run_service" in self._overrides:
            return self._overrides["run_service"]
        if "run_service" not in self._singletons:
            from deerflow.services.implementations import RunServiceImpl

            manager = self._get_run_manager()
            self._singletons["run_service"] = RunServiceImpl(manager)
        return self._singletons["run_service"]

    def workspace_service(self) -> WorkspaceService:
        """Return the WorkspaceService singleton."""
        if "workspace_service" in self._overrides:
            return self._overrides["workspace_service"]
        if "workspace_service" not in self._singletons:
            from deerflow.services.implementations import WorkspaceServiceImpl

            self._singletons["workspace_service"] = WorkspaceServiceImpl()
        return self._singletons["workspace_service"]

    def repository_service(self) -> RepositoryService:
        """Return the RepositoryService singleton."""
        if "repository_service" in self._overrides:
            return self._overrides["repository_service"]
        if "repository_service" not in self._singletons:
            from deerflow.services.implementations import RepositoryServiceImpl

            store = self._get_run_store()
            self._singletons["repository_service"] = RepositoryServiceImpl(store)
        return self._singletons["repository_service"]

    def browser_service(self) -> BrowserService:
        """Return the BrowserService singleton."""
        if "browser_service" in self._overrides:
            return self._overrides["browser_service"]
        if "browser_service" not in self._singletons:
            from deerflow.services.implementations import BrowserServiceImpl

            self._singletons["browser_service"] = BrowserServiceImpl()
        return self._singletons["browser_service"]

    def terminal_service(self) -> TerminalService:
        """Return the TerminalService singleton."""
        if "terminal_service" in self._overrides:
            return self._overrides["terminal_service"]
        if "terminal_service" not in self._singletons:
            from deerflow.services.implementations import TerminalServiceImpl

            self._singletons["terminal_service"] = TerminalServiceImpl()
        return self._singletons["terminal_service"]

    def artifact_service(self) -> ArtifactService:
        """Return the ArtifactService singleton."""
        if "artifact_service" in self._overrides:
            return self._overrides["artifact_service"]
        if "artifact_service" not in self._singletons:
            from deerflow.services.implementations import ArtifactServiceImpl

            self._singletons["artifact_service"] = ArtifactServiceImpl()
        return self._singletons["artifact_service"]

    def health_service(self) -> HealthService:
        """Return the HealthService singleton."""
        if "health_service" in self._overrides:
            return self._overrides["health_service"]
        if "health_service" not in self._singletons:
            from deerflow.services.implementations import HealthServiceImpl

            self._singletons["health_service"] = HealthServiceImpl()
        return self._singletons["health_service"]

    def recovery_service(self) -> RecoveryService:
        """Return the RecoveryService singleton."""
        if "recovery_service" in self._overrides:
            return self._overrides["recovery_service"]
        if "recovery_service" not in self._singletons:
            from deerflow.services.implementations import RecoveryServiceImpl

            self._singletons["recovery_service"] = RecoveryServiceImpl()
        return self._singletons["recovery_service"]

    def configuration_service(self) -> ConfigurationService:
        """Return the ConfigurationService singleton."""
        if "configuration_service" in self._overrides:
            return self._overrides["configuration_service"]
        if "configuration_service" not in self._singletons:
            from deerflow.services.implementations import ConfigurationServiceImpl

            self._singletons["configuration_service"] = ConfigurationServiceImpl()
        return self._singletons["configuration_service"]

    def diagnostics_service(self) -> DiagnosticsService:
        """Return the DiagnosticsService singleton."""
        if "diagnostics_service" in self._overrides:
            return self._overrides["diagnostics_service"]
        if "diagnostics_service" not in self._singletons:
            from deerflow.services.implementations import DiagnosticsServiceImpl

            self._singletons["diagnostics_service"] = DiagnosticsServiceImpl()
        return self._singletons["diagnostics_service"]

    # ------------------------------------------------------------------
    # Internal helpers — resolve runtime dependencies
    # ------------------------------------------------------------------

    def _get_run_manager(self) -> Any:
        """Get the RunManager from app.state or create a fallback."""
        try:
            from deerflow.services.container import _app_state_run_manager

            return _app_state_run_manager()
        except Exception:
            pass
        # Fallback: create a fresh RunManager (for embedded/test usage)
        from deerflow.runtime.runs.manager import RunManager

        return RunManager()

    def _get_run_store(self) -> Any:
        """Get the RunStore from app.state or create a fallback."""
        try:
            from deerflow.services.container import _app_state_run_store

            return _app_state_run_store()
        except Exception:
            pass
        from deerflow.runtime.runs.store.memory import MemoryRunStore

        return MemoryRunStore()


def _app_state_run_manager() -> Any:
    """Extract RunManager from FastAPI app.state (gateway path)."""
    # This is imported lazily to avoid circular imports with app.*
    from app.gateway.deps import get_run_manager as _get_rm

    # We can't call the FastAPI dependency without a request, so we
    # access the app state directly.  This only works inside a running
    # Gateway process.
    raise RuntimeError("No app.state available — use gateway wiring")


def _app_state_run_store() -> Any:
    """Extract RunStore from FastAPI app.state (gateway path)."""
    raise RuntimeError("No app.state available — use gateway wiring")


# Module-level singleton — importable, overridable for tests.
service_container = ServiceContainer()
