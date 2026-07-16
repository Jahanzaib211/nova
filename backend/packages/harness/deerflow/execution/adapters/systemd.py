"""Systemd adapter — unit management through the kernel.

Phase C7 — replaces direct ``subprocess.run(["sudo", "systemctl", ...])``
calls (tunnel recovery).  Sudo is policy-gated: only ``systemctl`` may be
the sudo target, enforced by the kernel's policy engine.
"""

from __future__ import annotations

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class SystemdAdapter(BaseAdapter):
    """Run systemctl unit operations, optionally via non-interactive sudo."""

    execution_class = ExecutionClass.SYSTEMD

    def _systemctl(
        self,
        *args: str,
        use_sudo: bool,
        timeout: float,
        correlation_id: str = "",
    ) -> ExecutionResult:
        argv = ("sudo", "-n", "systemctl", *args) if use_sudo else ("systemctl", *args)
        request = self._request(
            argv,
            timeout=timeout,
            intent=f"systemctl {' '.join(args)}",
            correlation_id=correlation_id,
        )
        return self._kernel.execute_sync(request)

    def restart(
        self,
        unit: str,
        *,
        use_sudo: bool = True,
        timeout: float = 30.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        return self._systemctl(
            "restart", unit, use_sudo=use_sudo, timeout=timeout, correlation_id=correlation_id
        )

    def reset_failed(
        self,
        unit: str,
        *,
        use_sudo: bool = True,
        timeout: float = 15.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        return self._systemctl(
            "reset-failed", unit, use_sudo=use_sudo, timeout=timeout, correlation_id=correlation_id
        )

    def is_active(
        self,
        unit: str,
        *,
        use_sudo: bool = True,
        timeout: float = 10.0,
        correlation_id: str = "",
    ) -> bool:
        result = self._systemctl(
            "is-active", unit, use_sudo=use_sudo, timeout=timeout, correlation_id=correlation_id
        )
        return result.ok and result.stdout.strip() == "active"
