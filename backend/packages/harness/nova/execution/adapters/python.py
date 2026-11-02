"""Python adapter — interpreter invocations through the kernel.

Phase C7 — runs Python scripts/snippets in the current interpreter's
environment (``sys.executable``), policy-gated and audited like every
other execution.
"""

from __future__ import annotations

import sys
from pathlib import Path

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class PythonAdapter(BaseAdapter):
    """Run Python code or scripts as subprocesses of the platform."""

    execution_class = ExecutionClass.PYTHON

    def run_code(
        self,
        code: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            (sys.executable, "-c", code),
            cwd=cwd,
            env=env,
            timeout=timeout,
            intent=intent or "python -c snippet",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)

    def run_script(
        self,
        script_path: str | Path,
        *args: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 300.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            (sys.executable, str(script_path), *args),
            cwd=cwd,
            env=env,
            timeout=timeout,
            intent=intent or f"python {Path(script_path).name}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)
