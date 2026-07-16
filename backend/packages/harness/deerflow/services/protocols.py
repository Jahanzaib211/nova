"""Protocol interfaces for the Nova service layer.

Every service is defined as a ``typing.Protocol`` — the structural-typing
equivalent of an ABC.  This means any object with the right method
signatures satisfies the protocol, regardless of its inheritance tree.

Phase C1 — interfaces only.  No implementation logic lives here.

Usage::

    from deerflow.services.protocols import RunService

    async def my_handler(run_svc: RunService) -> None:
        run = await run_svc.get("run-123")
        if run is not None:
            print(run.status)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from deerflow.services.types import (
    DiagnosticsRecord,
    HealthReport,
    ProbeResult,
    RecoveryAction,
    RunDetail,
    RunSummary,
    WorkspacePaths,
)

# ---------------------------------------------------------------------------
# RunService
# ---------------------------------------------------------------------------

class RunService(Protocol):
    """Lifecycle management for agent runs.

    Wraps ``RunManager`` + ``RunRepository`` behind a stable interface so
    callers never depend on the concrete manager implementation.

    Lifecycle expectations:
        - ``create`` allocates a new run and persists it to the store.
        - ``get`` returns the current state of a run (in-memory wins over store).
        - ``cancel`` interrupts or rolls back a running run.
        - ``list_by_thread`` returns runs in insertion order, newest first.

    Telemetry expectations:
        - Every mutation should emit structured logs at DEBUG level.
        - Correlation IDs must be propagated through all operations.
    """

    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str = "lead-agent",
        user_id: str | None = None,
        model_name: str | None = None,
        multitask_strategy: str = "reject",
        kwargs: dict[str, Any] | None = None,
    ) -> RunDetail:
        """Create a new run and persist it."""
        ...

    async def get(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> RunDetail | None:
        """Return the current state of a run, or None if not found."""
        ...

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[RunSummary]:
        """Return runs for a thread in insertion order (newest first)."""
        ...

    async def create_or_reject(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        on_disconnect: str = "cancel",
        metadata: dict[str, Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        multitask_strategy: str = "reject",
        model_name: str | None = None,
        user_id: str | None = None,
    ) -> RunDetail:
        """Atomically check for inflight runs and create a new one.

        For ``reject`` strategy, raises on conflict.  For ``interrupt``/``rollback``,
        cancels inflight runs before creating.
        """
        ...

    async def cancel(
        self,
        run_id: str,
        *,
        action: str = "interrupt",
    ) -> bool:
        """Cancel a running run. Returns True if cancellation was accepted."""
        ...

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Update the status of a run."""
        ...


# ---------------------------------------------------------------------------
# WorkspaceService
# ---------------------------------------------------------------------------

class WorkspaceService(Protocol):
    """Per-thread workspace directory management.

    Wraps ``ThreadDataMiddleware`` directory resolution logic.

    Lifecycle expectations:
        - ``resolve_paths`` returns the resolved workspace paths for a thread.
        - Directories are created on first access.

    Telemetry expectations:
        - Path resolution should be traceable via correlation_id.
    """

    def resolve_paths(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> WorkspacePaths:
        """Return the resolved workspace paths for a thread."""
        ...

    def ensure_directories(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> WorkspacePaths:
        """Ensure workspace directories exist and return their paths."""
        ...


# ---------------------------------------------------------------------------
# RepositoryService
# ---------------------------------------------------------------------------

class RepositoryService(Protocol):
    """Persistent run metadata storage.

    Wraps ``RunStore`` / ``RunRepository`` behind a stable interface.

    Lifecycle expectations:
        - ``put`` persists run metadata (creates or updates).
        - ``get`` retrieves run metadata by run_id.
        - ``list_by_thread`` returns all runs for a thread.

    Telemetry expectations:
        - All operations should log at DEBUG level.
    """

    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        status: str = "pending",
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Persist run metadata."""
        ...

    async def get(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve run metadata by run_id."""
        ...

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return all runs for a thread."""
        ...


# ---------------------------------------------------------------------------
# BrowserService
# ---------------------------------------------------------------------------

class BrowserService(Protocol):
    """Browser health checking and preview verification.

    Wraps ``browser_check`` module + circuit breaker logic.

    Lifecycle expectations:
        - ``check`` probes the browser/preview endpoint.
        - Circuit breaker opens after consecutive failures.

    Telemetry expectations:
        - Probe results should include latency.
    """

    async def check(
        self,
        url: str,
        *,
        timeout: float = 10.0,
    ) -> ProbeResult:
        """Probe a URL and return the health check result."""
        ...

    def is_circuit_open(self) -> bool:
        """Return True if the circuit breaker is open (failing fast)."""
        ...


# ---------------------------------------------------------------------------
# TerminalService
# ---------------------------------------------------------------------------

class TerminalService(Protocol):
    """Sandbox command execution.

    Wraps ``sandbox.execute_command`` with path translation.

    Lifecycle expectations:
        - ``execute`` runs a command in the thread's sandbox.
        - Commands are isolated per-thread.

    Telemetry expectations:
        - Command execution should be logged at DEBUG level.
        - Execution time should be measurable.
    """

    async def execute(
        self,
        command: str,
        *,
        thread_id: str,
        user_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute a command in the thread's sandbox.

        Returns:
            Dict with at least ``stdout``, ``stderr``, ``exit_code`` keys.
        """
        ...


# ---------------------------------------------------------------------------
# ArtifactService
# ---------------------------------------------------------------------------

class ArtifactService(Protocol):
    """Artifact storage and retrieval.

    Wraps the artifacts router logic.

    Lifecycle expectations:
        - ``get`` retrieves an artifact by path.
        - Artifacts are scoped to threads.

    Telemetry expectations:
        - Access should be logged at DEBUG level.
    """

    async def get(
        self,
        thread_id: str,
        path: str,
    ) -> tuple[bytes, str] | None:
        """Retrieve an artifact. Returns (content, mime_type) or None."""
        ...

    async def list(
        self,
        thread_id: str,
    ) -> list[str]:
        """List artifact paths for a thread."""
        ...


# ---------------------------------------------------------------------------
# HealthService
# ---------------------------------------------------------------------------

class HealthService(Protocol):
    """Health probe orchestration.

    Wraps the 12-probe watchdog (P1–P12).

    Lifecycle expectations:
        - ``check_all`` runs all probes and returns an aggregated report.
        - ``check_single`` runs one probe by name.

    Telemetry expectations:
        - Every probe result should include latency.
        - The aggregated report should be machine-parseable.
    """

    async def check_all(self) -> HealthReport:
        """Run all probes and return the aggregated health report."""
        ...

    async def check_single(
        self,
        probe_name: str,
    ) -> ProbeResult:
        """Run a single probe by name and return its result."""
        ...


# ---------------------------------------------------------------------------
# RecoveryService
# ---------------------------------------------------------------------------

class RecoveryService(Protocol):
    """Auto-recovery for known infrastructure failures.

    Wraps ``fix_tunnel``, container restart, and PM2 recovery logic.

    Lifecycle expectations:
        - ``recover`` attempts to fix a known failure mode.
        - Returns a ``RecoveryAction`` describing what was done.

    Telemetry expectations:
        - Every recovery attempt must be logged at WARNING level.
        - Success/failure must be recorded.
    """

    async def recover(
        self,
        component: str,
        *,
        force: bool = False,
    ) -> RecoveryAction:
        """Attempt to recover a failed component.

        Args:
            component: The component to recover (e.g., "tunnel", "gateway").
            force: If True, attempt recovery even if recent attempts failed.

        Returns:
            A RecoveryAction describing what was done.
        """
        ...


# ---------------------------------------------------------------------------
# ConfigurationService
# ---------------------------------------------------------------------------

class ConfigurationService(Protocol):
    """Application configuration access.

    Wraps ``get_app_config`` with caching and hot-reload.

    Lifecycle expectations:
        - ``get_config`` returns the current AppConfig.
        - Config is cached and auto-reloads on file changes.

    Telemetry expectations:
        - Config reloads should be logged at INFO level.
    """

    def get_config(self) -> Any:
        """Return the current AppConfig instance."""
        ...

    def get_config_value(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        """Return a specific config value by dotted key path."""
        ...


# ---------------------------------------------------------------------------
# DiagnosticsService
# ---------------------------------------------------------------------------

class DiagnosticsService(Protocol):
    """Streaming pipeline diagnostics.

    Wraps the diagnostics module for correlation_id tracking and
    pipeline observability.

    Lifecycle expectations:
        - ``record`` captures a diagnostics event.
        - ``read_recent`` returns recent records from the ring buffer.

    Telemetry expectations:
        - Records must carry correlation_id when available.
        - The ring buffer should be bounded (default 10_000).
    """

    def record(
        self,
        stage: str,
        *,
        run_id: str = "",
        thread_id: str = "",
        correlation_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Capture a diagnostics event."""
        ...

    def read_recent(
        self,
        limit: int = 100,
    ) -> list[DiagnosticsRecord]:
        """Return the most recent diagnostics records."""
        ...

    def register_correlation_id(
        self,
        thread_id: str,
        run_id: str,
        correlation_id: str,
    ) -> None:
        """Register a correlation_id for a thread/run pair."""
        ...

    def lookup_correlation_id(
        self,
        thread_id: str,
    ) -> str:
        """Look up the correlation_id for a thread."""
        ...


# ---------------------------------------------------------------------------
# WorkspaceIntelligenceService (Phase C9)
# ---------------------------------------------------------------------------


class WorkspaceIntelligenceService(Protocol):
    """Deterministic workspace understanding via typed graphs and symbol indexes.

    Replaces filesystem-based grep/shell exploration with indexed queries
    against the workspace graph, symbol index, and dependency graph.

    Lifecycle expectations:
        - ``scan`` builds or refreshes the workspace graph for a root path.
        - ``plan_search`` builds an execution plan for a search query.
        - ``plan_edit`` builds an execution plan for a file edit.
        - ``plan_run`` builds an execution plan for a named command.
        - ``execute_plan`` runs a validated plan through the ExecutionKernel.

    Telemetry expectations:
        - All operations emit structured logs with workspace correlation.
    """

    def scan(self, root_path: str) -> WorkspaceSnapshot:
        """Scan a workspace root and return a complete snapshot."""
        ...

    def plan_search(
        self,
        query: str,
        project_id: str | None = None,
        file_pattern: str | None = None,
    ) -> PlannerResult:
        """Build an execution plan for a search query."""
        ...

    def plan_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> PlannerResult:
        """Build an execution plan for a file edit."""
        ...

    def plan_run(
        self,
        command_name: str,
        project_id: str | None = None,
    ) -> PlannerResult:
        """Build an execution plan for a named command."""
        ...

    def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        """Execute a validated plan through the ExecutionKernel."""
        ...

    def get_snapshot(self, root_path: str) -> WorkspaceSnapshot | None:
        """Return the cached snapshot for a root path, or None if not yet scanned."""
        ...
