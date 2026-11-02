"""PM2 adapter — process-manager operations through the kernel.

Phase C7 — replaces direct ``subprocess.run(["pm2", ...])`` calls in the
recovery engine and ops tooling.
"""

from __future__ import annotations

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class Pm2Adapter(BaseAdapter):
    """Run pm2 process-manager commands."""

    execution_class = ExecutionClass.PM2

    def restart(
        self,
        app_name: str,
        *,
        timeout: float = 30.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            ("pm2", "restart", app_name),
            timeout=timeout,
            intent=f"pm2 restart {app_name}",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)

    def start(
        self,
        target: str,
        *,
        only: str | None = None,
        timeout: float = 30.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        args: list[str] = ["pm2", "start", target]
        if only:
            args += ["--only", only]
        request = self._request(
            tuple(args),
            timeout=timeout,
            intent=f"pm2 start {only or target}",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)

    def save(self, *, timeout: float = 15.0, correlation_id: str = "") -> ExecutionResult:
        request = self._request(
            ("pm2", "save"),
            timeout=timeout,
            intent="pm2 save",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)

    def describe(
        self,
        app_name: str,
        *,
        timeout: float = 10.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            ("pm2", "describe", app_name),
            timeout=timeout,
            intent=f"pm2 describe {app_name}",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)

    def jlist(self, *, timeout: float = 10.0, correlation_id: str = "") -> ExecutionResult:
        request = self._request(
            ("pm2", "jlist"),
            timeout=timeout,
            intent="pm2 jlist",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)
