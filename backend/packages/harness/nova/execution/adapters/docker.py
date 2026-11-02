"""Docker adapter — container-runtime CLI execution through the kernel.

Phase C7 — replaces direct ``subprocess.run(["docker", ...])`` calls in the
sandbox backends.  Supports both Docker and Apple Container: the runtime
program name is fixed at construction (mirrors LocalDockerBackend's
runtime detection, which itself now flows through this adapter).
"""

from __future__ import annotations

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import ExecutionClass, ExecutionResult


class DockerAdapter(BaseAdapter):
    """Run container-runtime CLI commands (``docker`` or ``container``)."""

    execution_class = ExecutionClass.DOCKER

    def __init__(self, kernel, runtime: str = "docker") -> None:  # noqa: ANN001 — protocol type
        super().__init__(kernel)
        if runtime not in ("docker", "container"):
            raise ValueError(f"unsupported container runtime: {runtime!r}")
        self._runtime = runtime

    @property
    def runtime(self) -> str:
        return self._runtime

    def cli(
        self,
        *args: str,
        timeout: float = 30.0,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        """Run ``<runtime> <args...>`` — the generic workhorse."""
        request = self._request(
            (self._runtime, *args),
            timeout=timeout,
            intent=intent or f"{self._runtime} {' '.join(args[:2])}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        return self._kernel.execute_sync(request)

    # ── Typed conveniences over cli() ─────────────────────────────────

    def version(self, timeout: float = 5.0) -> ExecutionResult:
        return self.cli("--version", timeout=timeout, intent=f"{self._runtime} version probe")

    def run_container(
        self,
        *args: str,
        timeout: float = 60.0,
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        return self.cli(
            "run",
            *args,
            timeout=timeout,
            intent=f"{self._runtime} run",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )

    def stop(
        self,
        target: str,
        *,
        timeout: float = 30.0,
        stop_timeout: int | None = None,
        correlation_id: str = "",
        thread_id: str = "",
    ) -> ExecutionResult:
        args: list[str] = ["stop"]
        if stop_timeout is not None:
            args += ["-t", str(stop_timeout)]
        args.append(target)
        return self.cli(
            *args,
            timeout=timeout,
            intent=f"{self._runtime} stop {target}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )

    def ps(
        self,
        *args: str,
        timeout: float = 10.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        return self.cli(
            "ps",
            *args,
            timeout=timeout,
            intent=f"{self._runtime} ps",
            correlation_id=correlation_id,
        )

    def inspect(
        self,
        *targets: str,
        timeout: float = 10.0,
        correlation_id: str = "",
    ) -> ExecutionResult:
        return self.cli(
            "inspect",
            *targets,
            timeout=timeout,
            intent=f"{self._runtime} inspect ({len(targets)} targets)",
            correlation_id=correlation_id,
        )
