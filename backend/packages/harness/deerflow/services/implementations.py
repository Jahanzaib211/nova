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
    """Thin wrapper around RunManager implementing RunService protocol."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

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

    async def cancel(
        self,
        run_id: str,
        *,
        action: str = "interrupt",
    ) -> bool:
        return await self._manager.cancel(run_id, action=action)

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        from deerflow.runtime.runs.schemas import RunStatus

        run_status = RunStatus(status) if status in [s.value for s in RunStatus] else RunStatus.PENDING
        await self._manager.set_status(run_id, run_status, error=error)


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
    """Thin wrapper implementing HealthService protocol."""

    def __init__(self, probes: dict[str, Any] | None = None) -> None:
        self._probes = probes or {}

    async def check_all(self) -> HealthReport:
        import time

        results: list[ProbeResult] = []
        for name, probe_fn in self._probes.items():
            start = time.monotonic()
            try:
                result = await probe_fn()
                latency = (time.monotonic() - start) * 1000
                results.append(ProbeResult(
                    name=name,
                    healthy=getattr(result, "healthy", True),
                    message=getattr(result, "message", ""),
                    latency_ms=latency,
                ))
            except Exception as exc:
                latency = (time.monotonic() - start) * 1000
                results.append(ProbeResult(
                    name=name,
                    healthy=False,
                    message=str(exc),
                    latency_ms=latency,
                ))
        healthy_count = sum(1 for r in results if r.healthy)
        return HealthReport(
            healthy=healthy_count == len(results),
            probes=results,
            probe_count=len(results),
            healthy_count=healthy_count,
        )

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
    """Thin wrapper implementing RecoveryService protocol."""

    def __init__(self) -> None:
        pass

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
    """Thin wrapper implementing DiagnosticsService protocol."""

    def __init__(self) -> None:
        pass

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
