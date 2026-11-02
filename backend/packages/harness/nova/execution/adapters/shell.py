"""Shell adapter — explicit-argv shell execution through the kernel.

Phase C7 — replaces every direct ``subprocess.run([shell, "-c", cmd])``
site.  Shell semantics are explicit: the command string becomes one argv
element of ``[shell, "-c", command]``; the kernel itself never uses
``shell=True``.
"""

from __future__ import annotations

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class ShellAdapter(BaseAdapter):
    """Run shell command strings or pre-built argv vectors."""

    execution_class = ExecutionClass.SHELL

    def run(
        self,
        command: str,
        *,
        shell_path: str = "/bin/sh",
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        """Run *command* via ``[shell_path, "-c", command]``."""
        request = self._request(
            (shell_path, "-c", command),
            cwd=cwd,
            env=env,
            timeout=timeout,
            intent=intent or f"shell: {command[:80]}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)

    def run_argv(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        """Run a pre-built argument vector (Windows shells, custom shells)."""
        request = self._request(
            tuple(argv),
            cwd=cwd,
            env=env,
            timeout=timeout,
            intent=intent or f"shell argv: {argv[0] if argv else ''}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)

    async def run_async(
        self,
        command: str,
        *,
        shell_path: str = "/bin/sh",
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            (shell_path, "-c", command),
            cwd=cwd,
            env=env,
            timeout=timeout,
            intent=intent or f"shell: {command[:80]}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return await self._kernel.execute(request)
