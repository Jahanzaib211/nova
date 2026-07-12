"""Nova typed service layer.

Phase C2 — architecture + dependency injection.

This package defines Protocol interfaces, typed return models, a canonical
RunState dataclass, and a service container for dependency injection.

Usage::

    from deerflow.services import service_container, RunState

    # Get services via container
    run_svc = service_container.run_service()
    detail = await run_svc.get("run-123")

    # Or use RunState directly
    state = RunState(run_id="abc", thread_id="t1", status="running")

    # For testing — override with mocks:
    service_container.override(run_service=my_mock_run_svc)
"""

from deerflow.services.container import ServiceContainer, service_container
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
from deerflow.services.types import (
    DiagnosticsRecord,
    HealthReport,
    ProbeResult,
    RecoveryAction,
    RunDetail,
    RunState,
    RunSummary,
    WorkspacePaths,
)

__all__ = [
    # Container
    "service_container",
    "ServiceContainer",
    # Protocols
    "RunService",
    "WorkspaceService",
    "RepositoryService",
    "BrowserService",
    "TerminalService",
    "ArtifactService",
    "HealthService",
    "RecoveryService",
    "ConfigurationService",
    "DiagnosticsService",
    # Types
    "RunState",
    "RunSummary",
    "RunDetail",
    "ProbeResult",
    "HealthReport",
    "RecoveryAction",
    "DiagnosticsRecord",
    "WorkspacePaths",
]
