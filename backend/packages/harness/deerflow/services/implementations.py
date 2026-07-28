"""Default service implementations — thin wrappers.

Phase C1 — these wrappers delegate directly to existing implementations.
No new business logic. No behavior changes. The objective is architectural
separation, not behavioral change.

Each wrapper is almost entirely forwarding calls.  The wrapper exists so
callers can depend on the Protocol interface instead of the concrete
implementation.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RunService — wraps RunManager
# ---------------------------------------------------------------------------


class RunServiceImpl:
    """Thin wrapper around RunManager implementing RunService protocol.

    Phase C3 — publishes lifecycle events on state transitions via EventBus.
    """

    def __init__(self, manager: Any, event_bus: Any | None = None) -> None:
        self._manager = manager
        self._event_bus = event_bus

    def _publish(self, event_class: type, **kwargs: Any) -> None:
        """Publish a lifecycle event if an event bus is configured."""
        if self._event_bus is not None:
            from deerflow.events.event import DomainEvent

            event = event_class(**kwargs)
            self._event_bus.publish(event)

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
        record = await self._manager.create(
            thread_id,
            assistant_id=assistant_id,
            user_id=user_id,
            model_name=model_name,
            multitask_strategy=multitask_strategy,
            **(kwargs or {}),
        )
        from deerflow.events.event import RunCreated

        self._publish(
            RunCreated,
            correlation_id=record.correlation_id,
            run_id=record.run_id,
            thread_id=record.thread_id,
            payload={
                "assistant_id": record.assistant_id,
                "model_name": record.model_name,
            },
        )
        return RunDetail(
            run_id=record.run_id,
            thread_id=record.thread_id,
            assistant_id=record.assistant_id,
            status=record.status.value if hasattr(record.status, "value") else str(record.status),
            correlation_id=record.correlation_id,
            model_name=record.model_name,
            created_at=record.created_at,
            error=record.error,
            total_tokens=record.total_tokens,
            message_count=record.message_count,
        )

    async def get(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> RunDetail | None:
        record = await self._manager.get(run_id, user_id=user_id)
        if record is None:
            return None
        return RunDetail(
            run_id=record.run_id,
            thread_id=record.thread_id,
            assistant_id=record.assistant_id,
            status=record.status.value if hasattr(record.status, "value") else str(record.status),
            correlation_id=record.correlation_id,
            model_name=record.model_name,
            created_at=record.created_at,
            error=record.error,
            total_tokens=record.total_tokens,
            message_count=record.message_count,
        )

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[RunSummary]:
        records = await self._manager.list_by_thread(thread_id, user_id=user_id, limit=limit)
        return [
            RunSummary(
                run_id=r.run_id,
                thread_id=r.thread_id,
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                correlation_id=r.correlation_id,
                model_name=r.model_name,
                created_at=r.created_at,
            )
            for r in records
        ]

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
        from deerflow.runtime.runs.schemas import DisconnectMode

        disconnect = DisconnectMode.cancel if on_disconnect == "cancel" else DisconnectMode.continue_
        record = await self._manager.create_or_reject(
            thread_id,
            assistant_id,
            on_disconnect=disconnect,
            metadata=metadata or {},
            kwargs=kwargs,
            multitask_strategy=multitask_strategy,
            model_name=model_name,
            user_id=user_id,
        )
        from deerflow.events.event import RunCreated

        self._publish(
            RunCreated,
            correlation_id=record.correlation_id,
            run_id=record.run_id,
            thread_id=record.thread_id,
            payload={
                "assistant_id": record.assistant_id,
                "model_name": record.model_name,
            },
        )
        return RunDetail(
            run_id=record.run_id,
            thread_id=record.thread_id,
            assistant_id=record.assistant_id,
            status=record.status.value if hasattr(record.status, "value") else str(record.status),
            correlation_id=record.correlation_id,
            model_name=record.model_name,
            created_at=record.created_at,
            error=record.error,
            total_tokens=record.total_tokens,
            message_count=record.message_count,
        )

    async def cancel(
        self,
        run_id: str,
        *,
        action: str = "interrupt",
    ) -> bool:
        result = await self._manager.cancel(run_id, action=action)
        if result:
            from deerflow.events.event import RunCancelled

            self._publish(RunCancelled, run_id=run_id, payload={"action": action})
        return result

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status
        from deerflow.runtime.runs.schemas import RunStatus

        run_status = RunStatus(status) if status in [s.value for s in RunStatus] else RunStatus.PENDING
        await self._manager.set_status(run_id, run_status, error=error)

        lifecycle = adapt_run_status(status)
        from deerflow.events import event as event_mod

        event_map = {
            RunLifecycleStatus.CREATED: event_mod.RunCreated,
            RunLifecycleStatus.INITIALIZING: event_mod.RunInitialized,
            RunLifecycleStatus.RUNNING: event_mod.RunStarted,
            RunLifecycleStatus.CHECKPOINT: event_mod.RunCheckpointCreated,
            RunLifecycleStatus.PAUSED: event_mod.RunPaused,
            RunLifecycleStatus.RESUMED: event_mod.RunResumed,
            RunLifecycleStatus.RECOVERING: event_mod.RunRecovering,
            RunLifecycleStatus.COMPLETED: event_mod.RunCompleted,
            RunLifecycleStatus.FAILED: event_mod.RunFailed,
            RunLifecycleStatus.CANCELLED: event_mod.RunCancelled,
            RunLifecycleStatus.ARCHIVED: event_mod.RunArchived,
        }
        event_class = event_map.get(lifecycle)
        if event_class is not None:
            payload: dict[str, Any] = {"status": status}
            if error:
                payload["error"] = error
            self._publish(event_class, run_id=run_id, payload=payload)


# ---------------------------------------------------------------------------
# WorkspaceService — wraps ThreadDataMiddleware paths
# ---------------------------------------------------------------------------


class WorkspaceServiceImpl:
    """Thin wrapper implementing WorkspaceService protocol."""

    def __init__(self, base_dir: str = ".deer-flow") -> None:
        self._base_dir = base_dir

    def resolve_paths(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> WorkspacePaths:
        from pathlib import Path

        uid = user_id or "default"
        base = Path(self._base_dir) / "users" / uid / "threads" / thread_id / "user-data"
        return WorkspacePaths(
            workspace=str(base / "workspace"),
            uploads=str(base / "uploads"),
            outputs=str(base / "outputs"),
            user_data_base=str(base),
        )

    def ensure_directories(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> WorkspacePaths:
        from pathlib import Path

        paths = self.resolve_paths(thread_id, user_id=user_id)
        for d in [paths.workspace, paths.uploads, paths.outputs]:
            Path(d).mkdir(parents=True, exist_ok=True)
        return paths


# ---------------------------------------------------------------------------
# RepositoryService — wraps RunStore
# ---------------------------------------------------------------------------


class RepositoryServiceImpl:
    """Thin wrapper implementing RepositoryService protocol."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        status: str = "pending",
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        await self._store.put(run_id, thread_id=thread_id, status=status, correlation_id=correlation_id, **kwargs)

    async def get(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        return await self._store.get(run_id)

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._store.list_by_thread(thread_id, limit=limit)


# ---------------------------------------------------------------------------
# BrowserService — wraps browser_check + circuit breaker
# ---------------------------------------------------------------------------


class BrowserServiceImpl:
    """Thin wrapper implementing BrowserService protocol."""

    def __init__(self) -> None:
        self._circuit_open = False
        self._failure_count = 0

    async def check(
        self,
        url: str,
        *,
        timeout: float = 10.0,
    ) -> ProbeResult:
        import time

        start = time.monotonic()
        try:
            from deerflow.sandbox.browser_check import check_browser_preview

            result = await check_browser_preview(url, timeout=timeout)
            latency = (time.monotonic() - start) * 1000
            self._failure_count = 0
            self._circuit_open = False
            return ProbeResult(
                name="browser",
                healthy=result.get("healthy", False),
                message=result.get("message", ""),
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            self._failure_count += 1
            if self._failure_count >= 3:
                self._circuit_open = True
            return ProbeResult(
                name="browser",
                healthy=False,
                message=str(exc),
                latency_ms=latency,
            )

    def is_circuit_open(self) -> bool:
        return self._circuit_open


# ---------------------------------------------------------------------------
# TerminalService — wraps sandbox.execute_command
# ---------------------------------------------------------------------------


class TerminalServiceImpl:
    """Thin wrapper implementing TerminalService protocol."""

    def __init__(self) -> None:
        pass

    async def execute(
        self,
        command: str,
        *,
        thread_id: str,
        user_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        from deerflow.sandbox.tools import bash as sandbox_bash

        result = await sandbox_bash(command, thread_id=thread_id, user_id=user_id, timeout=timeout)
        return {
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", -1),
        }


# ---------------------------------------------------------------------------
# ArtifactService — wraps artifact router logic
# ---------------------------------------------------------------------------


class ArtifactServiceImpl:
    """Thin wrapper implementing ArtifactService protocol."""

    def __init__(self) -> None:
        pass

    async def get(
        self,
        thread_id: str,
        path: str,
    ) -> tuple[bytes, str] | None:
        from pathlib import Path

        artifact_path = Path(f".deer-flow/users/default/threads/{thread_id}/user-data/outputs/{path}")
        if not artifact_path.exists():
            return None
        content = artifact_path.read_bytes()
        mime = "application/octet-stream"
        if path.endswith(".html"):
            mime = "text/html"
        elif path.endswith(".css"):
            mime = "text/css"
        elif path.endswith(".js"):
            mime = "application/javascript"
        elif path.endswith(".json"):
            mime = "application/json"
        elif path.endswith(".svg"):
            mime = "image/svg+xml"
        elif path.endswith(".png"):
            mime = "image/png"
        elif path.endswith(".jpg") or path.endswith(".jpeg"):
            mime = "image/jpeg"
        return content, mime

    async def list(
        self,
        thread_id: str,
    ) -> list[str]:
        from pathlib import Path

        outputs_dir = Path(f".deer-flow/users/default/threads/{thread_id}/user-data/outputs")
        if not outputs_dir.exists():
            return []
        return [str(p.relative_to(outputs_dir)) for p in outputs_dir.rglob("*") if p.is_file()]


# ---------------------------------------------------------------------------
# HealthService — wraps 12-probe watchdog
# ---------------------------------------------------------------------------


class HealthServiceImpl:
    """Thin wrapper implementing HealthService protocol.

    Phase C3 — publishes HealthChanged events when probe state transitions.
    """

    def __init__(self, probes: dict[str, Any] | None = None, event_bus: Any | None = None) -> None:
        self._probes = probes or {}
        self._event_bus = event_bus
        self._last_healthy: bool | None = None

    async def check_all(self) -> HealthReport:
        import time

        results: list[ProbeResult] = []
        for name, probe_fn in self._probes.items():
            start = time.monotonic()
            try:
                result = await probe_fn()
                latency = (time.monotonic() - start) * 1000
                results.append(
                    ProbeResult(
                        name=name,
                        healthy=getattr(result, "healthy", True),
                        message=getattr(result, "message", ""),
                        latency_ms=latency,
                    )
                )
            except Exception as exc:
                latency = (time.monotonic() - start) * 1000
                results.append(
                    ProbeResult(
                        name=name,
                        healthy=False,
                        message=str(exc),
                        latency_ms=latency,
                    )
                )
        healthy_count = sum(1 for r in results if r.healthy)
        report = HealthReport(
            healthy=healthy_count == len(results),
            probes=results,
            probe_count=len(results),
            healthy_count=healthy_count,
        )
        # Publish HealthChanged if overall state transitioned
        if self._event_bus is not None and self._last_healthy != report.healthy:
            from deerflow.events.event import HealthChanged

            self._event_bus.publish(
                HealthChanged(
                    payload={
                        "healthy": report.healthy,
                        "probe_count": report.probe_count,
                        "healthy_count": report.healthy_count,
                    },
                )
            )
            self._last_healthy = report.healthy
        return report

    async def check_single(
        self,
        probe_name: str,
    ) -> ProbeResult:
        import time

        probe_fn = self._probes.get(probe_name)
        if probe_fn is None:
            return ProbeResult(name=probe_name, healthy=False, message=f"Unknown probe: {probe_name}")
        start = time.monotonic()
        try:
            result = await probe_fn()
            latency = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=probe_name,
                healthy=getattr(result, "healthy", True),
                message=getattr(result, "message", ""),
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ProbeResult(name=probe_name, healthy=False, message=str(exc), latency_ms=latency)


# ---------------------------------------------------------------------------
# RecoveryService — wraps fix_tunnel, container restart
# ---------------------------------------------------------------------------


class RecoveryServiceImpl:
    """Thin wrapper implementing RecoveryService protocol.

    Phase C4 — wraps existing fix_tunnel, PM2 restart, and container
    restart logic.  Optionally delegates to RecoveryEngine for
    event-driven recovery orchestration.
    """

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    async def recover(
        self,
        component: str,
        *,
        force: bool = False,
    ) -> RecoveryAction:
        if component == "tunnel":
            try:
                from scripts.healthcheck_daemon import fix_tunnel

                fix_tunnel()
                return RecoveryAction(component="tunnel", action="restart", success=True)
            except Exception as exc:
                return RecoveryAction(component="tunnel", action="restart", success=False, message=str(exc))
        return RecoveryAction(
            component=component,
            action="unknown",
            success=False,
            message=f"No recovery handler for component: {component}",
        )

    @property
    def engine(self) -> Any:
        """Return the RecoveryEngine if configured."""
        return self._engine


# ---------------------------------------------------------------------------
# ConfigurationService — wraps get_app_config
# ---------------------------------------------------------------------------


class ConfigurationServiceImpl:
    """Thin wrapper implementing ConfigurationService protocol."""

    def __init__(self) -> None:
        pass

    def get_config(self) -> Any:
        from deerflow.config import get_app_config

        return get_app_config()

    def get_config_value(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        config = self.get_config()
        parts = key.split(".")
        obj = config
        for part in parts:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                return default
        return obj


# ---------------------------------------------------------------------------
# DiagnosticsService — wraps diagnostics module
# ---------------------------------------------------------------------------


class DiagnosticsServiceImpl:
    """Thin wrapper implementing DiagnosticsService protocol.

    Phase C3 — can subscribe to EventBus and record all domain events
    as diagnostics records.
    """

    def __init__(self) -> None:
        pass

    def subscribe_to_events(self, bus: Any) -> None:
        """Subscribe to all DomainEvent subclasses and record them."""
        from deerflow.events.event import DomainEvent

        def _on_event(event: DomainEvent) -> None:
            self.record(
                f"event:{event.event_type}",
                run_id=event.run_id,
                thread_id=event.thread_id,
                correlation_id=event.correlation_id,
                extra={"event_id": event.event_id, **event.payload},
            )

        bus.subscribe(DomainEvent, _on_event)

    def record(
        self,
        stage: str,
        *,
        run_id: str = "",
        thread_id: str = "",
        correlation_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        from deerflow.runtime.stream_bridge.diagnostics import Diagnostics

        Diagnostics.record(
            stage,
            run_id=run_id,
            thread_id=thread_id,
            correlation_id=correlation_id,
            extra=extra or {},
        )

    def read_recent(
        self,
        limit: int = 100,
    ) -> list[DiagnosticsRecord]:
        from deerflow.runtime.stream_bridge.diagnostics import Diagnostics

        records = Diagnostics.read_recent(limit)
        return [
            DiagnosticsRecord(
                seq=getattr(r, "seq", 0),
                stage=getattr(r, "stage", ""),
                run_id=getattr(r, "run_id", ""),
                thread_id=getattr(r, "thread_id", ""),
                correlation_id=getattr(r, "correlation_id", ""),
                wall_iso=getattr(r, "wall_iso", ""),
                extra=getattr(r, "extra", {}),
            )
            for r in records
        ]

    def register_correlation_id(
        self,
        thread_id: str,
        run_id: str,
        correlation_id: str,
    ) -> None:
        from deerflow.runtime.stream_bridge.diagnostics import register_correlation_id

        register_correlation_id(thread_id, run_id, correlation_id)

    def lookup_correlation_id(
        self,
        thread_id: str,
    ) -> str:
        from deerflow.runtime.stream_bridge.diagnostics import lookup_correlation_id

        return lookup_correlation_id(thread_id) or ""


# ---------------------------------------------------------------------------
# WorkspaceIntelligenceService (Phase C9)
# ---------------------------------------------------------------------------


class WorkspaceIntelligenceServiceImpl:
    """Deterministic workspace understanding via typed graphs.

    Wraps the WIK components: BoundedWalker, FingerprintDetector,
    ProjectDetector, LanguageDetector, CommandDetector, PythonParser,
    JSParser, WorkspaceGraph, SymbolIndex, DependencyGraph,
    CommandRegistry, WorkspacePlanner, PlanValidator, RiskAnalyzer,
    and WorkspaceCache.

    Emits domain events and records metrics for all WIK operations.
    """

    def __init__(
        self,
        base_dir: str = ".deer-flow",
        cache_ttl_seconds: float = 3600.0,
    ) -> None:
        self._base_dir = base_dir
        self._cache_ttl = cache_ttl_seconds
        self._snapshots: dict[str, WorkspaceSnapshot] = {}
        self._snapshot_times: dict[str, float] = {}
        # scan_async runs scan() on worker threads (asyncio.to_thread), so the
        # snapshot maps need a lock (2026-07 audit C1/B6).
        self._snapshots_lock = threading.RLock()
        self._metrics = self._make_metrics()
        self._event_bus = None
        self._scan_start_time: float = 0.0

    def _get_event_bus(self):
        if self._event_bus is None:
            try:
                from deerflow.events.bus import event_bus as _eb

                self._event_bus = _eb
            except Exception:
                self._event_bus = None
        return self._event_bus

    def _make_metrics(self):
        from deerflow.workspace.metrics import WIKMetrics

        return WIKMetrics()

    def _emit(self, event) -> None:
        bus = self._get_event_bus()
        if bus is not None:
            try:
                bus.publish(event)
            except Exception:
                pass

    def _record_scan(self, root_path, file_count, project_count, symbol_count, command_count, duration_ms, language_distribution=None):
        self._metrics.record_scan(file_count, project_count, symbol_count, duration_ms, language_distribution)
        from deerflow.workspace.events import WorkspaceScanned

        self._emit(
            WorkspaceScanned(
                root_path=root_path,
                file_count=file_count,
                project_count=project_count,
                symbol_count=symbol_count,
                command_count=command_count,
                scan_duration_ms=duration_ms,
                language_distribution=language_distribution or {},
            )
        )

    def _record_plan_built(self, plan, symbols_found):
        if plan:
            self._metrics.record_plan_built(len(plan.steps), True)
            from deerflow.workspace.events import PlanBuilt

            self._emit(
                PlanBuilt(
                    root_path="",
                    plan_id=plan.plan_id,
                    plan_title=getattr(plan, "goal", "") or "",
                    step_count=len(plan.steps),
                    plan_valid=True,
                    risk_level=plan.risk_level.value if hasattr(plan.risk_level, "value") else str(plan.risk_level),
                    symbols_found=tuple(symbols_found),
                )
            )

    def _record_cache_hit(self, root_path: str = ""):
        self._metrics.record_cache_hit()
        from deerflow.workspace.events import CacheHit

        self._emit(CacheHit(root_path=root_path, cache_key="", hit_count=self._metrics._cache_hits))

    def _record_cache_miss(self, root_path: str = ""):
        self._metrics.record_cache_miss()
        from deerflow.workspace.events import CacheMiss

        self._emit(CacheMiss(root_path=root_path, cache_key=""))

    def scan(self, root_path: str, force_refresh: bool = False) -> WorkspaceSnapshot:
        """Scan a workspace root and return a complete snapshot.

        Blocking (filesystem walk + AST parsing) — async callers must use
        :meth:`scan_async`, which offloads via ``asyncio.to_thread``.
        A snapshot younger than ``cache_ttl_seconds`` is returned as-is
        unless ``force_refresh`` is set.
        """
        from pathlib import Path

        from deerflow.workspace.detectors import (
            CommandDetector,
            FingerprintDetector,
            LanguageDetector,
            ProjectDetector,
        )
        from deerflow.workspace.graph import CommandRegistry, DependencyGraph, SymbolIndex, WorkspaceGraph
        from deerflow.workspace.models.workspace_snapshot import WorkspaceSnapshot
        from deerflow.workspace.parsers import JSParser, PythonParser
        from deerflow.workspace.scanners import BoundedWalker, TraversalLimit

        root = Path(root_path)

        with self._snapshots_lock:
            cached = self._snapshots.get(str(root))
            cached_at = self._snapshot_times.get(str(root), 0.0)
        if cached is not None and not force_refresh and (time.monotonic() - cached_at) < self._cache_ttl:
            self._record_cache_hit(str(root))
            return cached
        if cached is not None:
            self._record_cache_miss(str(root))

        limit = TraversalLimit(max_depth=6, max_files=50000, max_duration_seconds=15.0)

        fp_detector = FingerprintDetector()
        fp = fp_detector.detect(root)

        proj_detector = ProjectDetector()
        projects = proj_detector.find_all(root)

        cmd_detector = CommandDetector()
        commands = cmd_detector.build_registry(projects)

        graph = WorkspaceGraph()
        graph.build_from_projects(projects)

        symbols = SymbolIndex()
        deps = DependencyGraph()

        py_parser = PythonParser()
        js_parser = JSParser()

        walker = BoundedWalker(root=root, limits=limit)
        start_time = time.monotonic()
        for entry in walker.walk():
            for fname in entry.files:
                fpath = Path(entry.root) / fname
                suffix = fpath.suffix.lower()
                if suffix in (".py",):
                    syms = py_parser.parse_file(fpath)
                    symbols.add_many(syms)
                elif suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
                    syms = js_parser.parse_file(fpath)
                    symbols.add_many(syms)
        duration_ms = (time.monotonic() - start_time) * 1000.0

        cmd_reg = CommandRegistry()
        cmd_reg.register_many(commands)

        all_symbols = [s for syms in symbols._by_name.values() for s in syms]
        snapshot = WorkspaceSnapshot(
            thread_id=str(root),
            fingerprint=fp,
            projects=tuple(projects),
            symbols=tuple(all_symbols),
            commands=tuple(commands),
            graph_nodes=tuple(graph._nodes_by_id.values()),
            graph_edges=tuple(graph._edges),
            traversal_count=walker.stats.files_visited,
            duration_ms=duration_ms,
        )

        with self._snapshots_lock:
            self._snapshots[str(root)] = snapshot
            self._snapshot_times[str(root)] = time.monotonic()
        self._record_scan(
            root_path=str(root),
            file_count=walker.stats.files_visited,
            project_count=len(projects),
            symbol_count=len(all_symbols),
            command_count=len(commands),
            duration_ms=duration_ms,
        )
        return snapshot

    async def scan_async(self, root_path: str, force_refresh: bool = False) -> WorkspaceSnapshot:
        """Async entry point: run the blocking scan off the event loop."""
        import asyncio

        return await asyncio.to_thread(self.scan, root_path, force_refresh=force_refresh)

    def plan_search(
        self,
        query: str,
        project_id: str | None = None,
        file_pattern: str | None = None,
    ) -> PlannerResult:
        """Build an execution plan for a search query."""
        from deerflow.workspace.graph import CommandRegistry, DependencyGraph, SymbolIndex, WorkspaceGraph
        from deerflow.workspace.planner import WorkspacePlanner

        graph = WorkspaceGraph()
        symbols = SymbolIndex()
        deps = DependencyGraph()
        cmd_reg = CommandRegistry()

        for snap in self._snapshots.values():
            for node in snap.graph_nodes:
                graph.add_node(node)
            for sym in snap.symbols:
                symbols.add(sym)
            for cmd in snap.commands:
                cmd_reg.register(cmd)

        planner = WorkspacePlanner(
            graph=graph,
            symbols=symbols,
            deps=deps,
            commands=cmd_reg,
        )
        result = planner.plan_search(query, project_id, file_pattern)
        if result.plan:
            self._record_plan_built(result.plan, result.symbols_found)
        return result

    def plan_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> PlannerResult:
        """Build an execution plan for a file edit."""
        from deerflow.workspace.graph import DependencyGraph, WorkspaceGraph
        from deerflow.workspace.planner import WorkspacePlanner

        deps = DependencyGraph()
        graph = WorkspaceGraph()
        for snap in self._snapshots.values():
            for node in snap.graph_nodes:
                graph.add_node(node)

        planner = WorkspacePlanner(
            graph=graph,
            symbols=self._get_symbol_index(),
            deps=deps,
            commands=self._get_command_registry(),
        )
        result = planner.plan_edit_file(file_path, old_string, new_string)
        if result.plan:
            self._record_plan_built(result.plan, [])
        return result

    def plan_run(
        self,
        command_name: str,
        project_id: str | None = None,
    ) -> PlannerResult:
        """Build an execution plan for a named command."""
        from deerflow.workspace.graph import CommandRegistry, DependencyGraph, WorkspaceGraph
        from deerflow.workspace.planner import WorkspacePlanner

        cmd_reg = CommandRegistry()
        for snap in self._snapshots.values():
            for cmd in snap.commands:
                cmd_reg.register(cmd)

        planner = WorkspacePlanner(
            graph=WorkspaceGraph(),
            symbols=self._get_symbol_index(),
            deps=DependencyGraph(),
            commands=cmd_reg,
        )
        result = planner.plan_run_command(command_name, project_id)
        if result.plan:
            self._record_plan_built(result.plan, [])
        return result

    def _get_symbol_index(self) -> SymbolIndex:
        from deerflow.workspace.graph import SymbolIndex

        symbols = SymbolIndex()
        for snap in self._snapshots.values():
            for sym in snap.symbols:
                symbols.add(sym)
        return symbols

    def _get_command_registry(self) -> CommandRegistry:
        from deerflow.workspace.graph import CommandRegistry

        cmd_reg = CommandRegistry()
        for snap in self._snapshots.values():
            for cmd in snap.commands:
                cmd_reg.register(cmd)
        return cmd_reg

    def execute_plan(self, plan: ExecutionPlan, approved: bool = False) -> ExecutionResult:
        """Execute a validated plan through the ExecutionKernel.

        Gate order (2026-07 audit B5):
        1. PlanValidator — structural errors raise ValueError.
        2. RiskAnalyzer — CRITICAL plans are always refused; HIGH plans are
           refused unless the caller passes ``approved=True``.

        Converts WIK ExecutionSteps to ExecutionRequests and executes
        them sequentially through the Phase C8 ExecutionKernel.
        """
        from deerflow.events.bus import event_bus
        from deerflow.execution import ExecutionKernel
        from deerflow.execution.models import ExecutionClass, ExecutionRequest, ResourceLimits
        from deerflow.workspace.models.execution_plan import RiskLevel, StepKind
        from deerflow.workspace.planner.plan_validator import PlanValidator
        from deerflow.workspace.planner.risk_analyzer import RiskAnalyzer

        validation = PlanValidator().validate(plan)
        if not validation.is_valid:
            raise ValueError(f"Plan failed validation: {'; '.join(validation.errors)}")

        risk = RiskAnalyzer().analyze_plan(plan)
        if risk == RiskLevel.CRITICAL:
            raise PermissionError(f"Plan '{plan.plan_id}' is CRITICAL risk and cannot be executed")
        if risk.order >= RiskLevel.HIGH.order and not approved:
            raise PermissionError(f"Plan '{plan.plan_id}' is {risk.value} risk and requires explicit approval")

        if not plan.steps:
            from deerflow.execution.models import ExecutionResult

            return ExecutionResult(
                execution_id="",
                status="succeeded",
                exit_code=0,
                stdout="",
                stderr="",
                duration_ms=0.0,
            )

        interactive_kinds = {StepKind.CONFIRM, StepKind.ASK_USER}
        kernel = ExecutionKernel(event_bus=event_bus)
        last_result = None
        for step in plan.steps:
            if step.kind in interactive_kinds:
                continue
            execution_class = self._step_kind_to_class(step.kind, argv=step.argv)
            request = ExecutionRequest(
                argv=step.argv,
                execution_class=execution_class,
                cwd=step.cwd or None,
                limits=ResourceLimits(timeout_seconds=30.0),
                intent=step.description,
            )
            last_result = kernel.execute_sync(request)
        return last_result

    def _step_kind_to_class(self, kind: StepKind, argv: tuple[str, ...] = ()) -> ExecutionClass:
        from deerflow.execution.models import ExecutionClass

        program = str(argv[0]).rsplit("/", 1)[-1] if argv else ""
        if program == "git":
            return ExecutionClass.GIT
        if program.startswith("python"):
            return ExecutionClass.PYTHON
        if program == "docker":
            return ExecutionClass.DOCKER
        return ExecutionClass.SHELL

    def get_snapshot(self, root_path: str) -> WorkspaceSnapshot | None:
        """Return the cached snapshot for a root path, or None if not yet scanned."""
        with self._snapshots_lock:
            return self._snapshots.get(root_path)

    def analyze_impact(self, root_path: str, files: list[str]) -> dict[str, Any]:
        """Return what a change to ``files`` touches, from the cached snapshot.

        Snapshot-derived (no filesystem access): symbols defined in the files,
        projects containing them, and the commands of those projects.
        Returns ``{"scanned": False}`` when no snapshot exists yet.
        """
        snapshot = self.get_snapshot(root_path)
        if snapshot is None:
            return {"scanned": False, "files": list(files), "symbols": [], "projects": [], "commands": []}

        # Callers (file tree, diff tooling) send workspace-relative paths;
        # snapshot symbols carry scanner-produced paths that may be absolute.
        # Normalize both sides to relative-under-root before matching.
        root_prefix = str(root_path).rstrip("/") + "/"

        def _rel(path: str) -> str:
            if path.rstrip("/") == str(root_path).rstrip("/"):
                return ""
            if path.startswith(root_prefix):
                return path[len(root_prefix) :]
            return path.lstrip("./")

        normalized = {_rel(f.rstrip("/")) for f in files}

        def matches(path: str) -> bool:
            rel = _rel(path)
            # exact file match, or the changed entry is a directory prefix
            return rel in normalized or any(rel.startswith(f + "/") for f in normalized)

        touched_symbols = [s for s in snapshot.symbols if matches(s.file_path)]
        touched_project_ids = {s.project_id for s in touched_symbols if s.project_id}
        touched_project_ids |= {p.project_id for p in snapshot.projects if any(f.startswith(_rel(p.root_path).rstrip("/") + "/") or f == _rel(p.root_path) or _rel(p.root_path) == "" for f in normalized)}
        touched_commands = [c for c in snapshot.commands if c.project_id in touched_project_ids]

        return {
            "scanned": True,
            "files": list(files),
            "symbols": [{"name": s.name, "kind": s.kind.value, "file_path": s.file_path, "fqn": s.fqn} for s in touched_symbols],
            "projects": sorted(touched_project_ids),
            "commands": [{"name": c.name, "kind": c.kind.value, "project_id": c.project_id} for c in touched_commands],
        }
