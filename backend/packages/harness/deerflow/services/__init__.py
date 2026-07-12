"""Nova typed service layer.

Phase C1 — architecture preparation only.

This package defines Protocol interfaces and typed return models for
Nova's core services.  The interfaces enable dependency injection and
make the architecture testable without changing any runtime behavior.

Usage::

    from deerflow.services.protocols import RunService
    from deerflow.services.types import RunDetail

    async def handler(run_svc: RunService) -> None:
        detail = await run_svc.get("run-123")
        if detail is not None:
            print(detail.status)
"""

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
    RunSummary,
    WorkspacePaths,
)

__all__ = [
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
    "RunSummary",
    "RunDetail",
    "ProbeResult",
    "HealthReport",
    "RecoveryAction",
    "DiagnosticsRecord",
    "WorkspacePaths",
]
