"""Git adapter — all git plumbing flows through the kernel.

Phase C7 — replaces the ad-hoc ``_git()`` helpers scattered through the
repository (sandbox/review.py and friends).
"""

from __future__ import annotations

from pathlib import Path

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class GitAdapter(BaseAdapter):
    """Run git commands against a working directory."""

    execution_class = ExecutionClass.GIT

    def run(
        self,
        work_dir: str | Path,
        *args: str,
        timeout: float = 8.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        request = self._request(
            ("git", "-C", str(work_dir), *args),
            timeout=timeout,
            intent=intent or f"git {' '.join(args[:2])}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)

    def run_capture(
        self,
        work_dir: str | Path,
        *args: str,
        timeout: float = 8.0,
        correlation_id: str = "",
        thread_id: str = "",
    ) -> tuple[int, str]:
        """Back-compat shape: ``(exit_code, stdout + stderr)``.

        Kernel-level failures (denied, timeout, missing binary) surface as
        ``(1, error message)`` — the same contract the old ``_git()``
        helpers exposed.
        """
        result = self.run(
            work_dir,
            *args,
            timeout=timeout,
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        if result.exit_code is None:
            return 1, result.error or "execution failed"
        return result.exit_code, (result.stdout or "") + (result.stderr or "")
