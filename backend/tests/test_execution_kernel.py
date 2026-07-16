"""Unit + integration tests for the Nova Execution Kernel (Phase C7).

Run:
    cd backend && uv run pytest tests/test_execution_kernel.py -v
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from deerflow.events.bus import EventBus
from deerflow.execution import (
    ExecutionClass,
    ExecutionDeniedError,
    ExecutionKernel,
    ExecutionRequest,
    ExecutionStatus,
    PolicyEngine,
    ReplayEngine,
    ResourceLimits,
    ResourceManager,
)
from deerflow.execution.adapters import (
    DockerAdapter,
    GitAdapter,
    Pm2Adapter,
    PythonAdapter,
    ShellAdapter,
    SystemdAdapter,
)
from deerflow.execution.policy import ClassPolicy

# ── Kernel core ──────────────────────────────────────────────────────────────


def test_execute_sync_success():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("echo", "hello"), execution_class=ExecutionClass.SHELL)
    )
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.duration_ms > 0


def test_execute_sync_nonzero_exit_is_failed_not_raised():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("/bin/sh", "-c", "exit 3"), execution_class=ExecutionClass.SHELL)
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.exit_code == 3
    assert "exit code 3" in (result.error or "")


def test_execute_sync_missing_program_is_failed():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("nova-no-such-binary-xyz",), execution_class=ExecutionClass.SHELL)
    )
    assert result.status is ExecutionStatus.FAILED
    assert "not found" in (result.error or "")


def test_execute_sync_timeout_escalates_and_reports():
    kernel = ExecutionKernel()
    started = time.monotonic()
    result = kernel.execute_sync(
        ExecutionRequest(
            argv=("/bin/sh", "-c", "sleep 30"),
            execution_class=ExecutionClass.SHELL,
            limits=ResourceLimits(timeout=0.5, grace_period=1.0),
        )
    )
    elapsed = time.monotonic() - started
    assert result.status is ExecutionStatus.TIMED_OUT
    assert "timed out" in (result.error or "")
    assert elapsed < 10  # TERM→KILL escalation, no full 30s wait


def test_execute_sync_stdin_piped():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("cat",), execution_class=ExecutionClass.SHELL, stdin="from-stdin")
    )
    assert result.ok
    assert result.stdout == "from-stdin"


def test_execute_sync_output_truncation():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(
            argv=("/bin/sh", "-c", "yes x | head -c 100000"),
            execution_class=ExecutionClass.SHELL,
            limits=ResourceLimits(max_output_bytes=1000),
        )
    )
    assert result.ok
    assert len(result.stdout.encode()) < 2000
    assert result.stdout.endswith("[output truncated]")


def test_execute_async_facade():
    kernel = ExecutionKernel()

    async def go():
        return await kernel.execute(
            ExecutionRequest(argv=("echo", "async"), execution_class=ExecutionClass.SHELL)
        )

    result = asyncio.run(go())
    assert result.ok
    assert result.stdout.strip() == "async"


def test_empty_argv_denied():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(ExecutionRequest(argv=()))
    assert result.status is ExecutionStatus.DENIED
    assert "empty argv" in (result.error or "")


# ── Policy engine ────────────────────────────────────────────────────────────


def test_policy_denies_program_outside_allowlist():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("rm", "-rf", "/tmp/x"), execution_class=ExecutionClass.GIT)
    )
    assert result.status is ExecutionStatus.DENIED
    assert "not allowed" in (result.error or "")


def test_policy_denies_sudo_for_non_systemd_classes():
    kernel = ExecutionKernel()
    result = kernel.execute_sync(
        ExecutionRequest(argv=("sudo", "rm", "-rf", "/"), execution_class=ExecutionClass.SHELL)
    )
    assert result.status is ExecutionStatus.DENIED
    assert "sudo" in (result.error or "")


def test_policy_allows_sudo_systemctl_only():
    engine = PolicyEngine()
    ok = engine.evaluate(
        ExecutionRequest(
            argv=("sudo", "-n", "systemctl", "restart", "x.service"),
            execution_class=ExecutionClass.SYSTEMD,
        )
    )
    assert ok.allowed
    bad = engine.evaluate(
        ExecutionRequest(
            argv=("sudo", "-n", "bash", "-c", "true"),
            execution_class=ExecutionClass.SYSTEMD,
        )
    )
    assert not bad.allowed


def test_policy_clamps_timeout():
    engine = PolicyEngine(policies={ExecutionClass.GIT: ClassPolicy(max_timeout=5.0, allowed_programs=("git",))})
    decision = engine.evaluate(
        ExecutionRequest(
            argv=("git", "status"),
            execution_class=ExecutionClass.GIT,
            limits=ResourceLimits(timeout=9999),
        )
    )
    assert decision.allowed
    assert decision.effective_timeout == 5.0


# ── Resource manager ─────────────────────────────────────────────────────────


def test_resource_saturation_denies():
    kernel = ExecutionKernel(
        resource_manager=ResourceManager(slots={ExecutionClass.SHELL: 1})
    )
    # Hold the single slot open with a background execution.
    import threading

    hold = threading.Thread(
        target=kernel.execute_sync,
        args=(
            ExecutionRequest(
                argv=("/bin/sh", "-c", "sleep 2"),
                execution_class=ExecutionClass.SHELL,
                limits=ResourceLimits(timeout=5),
            ),
        ),
    )
    hold.start()
    time.sleep(0.3)
    result = kernel.execute_sync(
        ExecutionRequest(
            argv=("echo", "x"),
            execution_class=ExecutionClass.SHELL,
            limits=ResourceLimits(queue_timeout=0.1),
        )
    )
    hold.join()
    assert result.status is ExecutionStatus.DENIED
    assert "saturated" in (result.error or "")


def test_resource_slot_released_after_execution():
    manager = ResourceManager(slots={ExecutionClass.SHELL: 2})
    kernel = ExecutionKernel(resource_manager=manager)
    for _ in range(5):
        assert kernel.execute_sync(
            ExecutionRequest(argv=("true",), execution_class=ExecutionClass.SHELL)
        ).ok
    assert manager.in_flight(ExecutionClass.SHELL) == 0


# ── Audit engine ─────────────────────────────────────────────────────────────


def test_audit_records_every_execution_with_valid_chain():
    kernel = ExecutionKernel()
    for i in range(3):
        kernel.execute_sync(
            ExecutionRequest(argv=("echo", str(i)), execution_class=ExecutionClass.SHELL)
        )
    assert kernel.audit_engine.size == 3
    assert kernel.audit_engine.verify_chain()
    records = kernel.audit_engine.read_recent()
    assert [r.seq for r in records] == [1, 2, 3]
    assert all(r.record_hash for r in records)


def test_audit_never_stores_env_values():
    kernel = ExecutionKernel()
    kernel.execute_sync(
        ExecutionRequest(
            argv=("true",),
            execution_class=ExecutionClass.SHELL,
            env={"SECRET_TOKEN": "hunter2", "PATH": "/usr/bin"},
        )
    )
    record = kernel.audit_engine.read_recent()[-1]
    assert "SECRET_TOKEN" in record.env_keys
    assert "hunter2" not in str(record.to_dict())


# ── Events ───────────────────────────────────────────────────────────────────


def test_lifecycle_events_emitted_in_order():
    bus = EventBus()
    kernel = ExecutionKernel(event_bus=bus)
    kernel.execute_sync(
        ExecutionRequest(
            argv=("echo", "evt"),
            execution_class=ExecutionClass.SHELL,
            correlation_id="corr-99",
        )
    )
    names = [e.event_type for e in bus.replay()]
    assert names == ["ExecutionRequested", "ExecutionStarted", "ExecutionCompleted"]
    assert all(e.correlation_id == "corr-99" for e in bus.replay())


def test_denied_event_emitted():
    bus = EventBus()
    kernel = ExecutionKernel(event_bus=bus)
    kernel.execute_sync(
        ExecutionRequest(argv=("rm", "x"), execution_class=ExecutionClass.GIT)
    )
    names = [e.event_type for e in bus.replay()]
    assert names[-1] == "ExecutionDenied"


# ── Metrics ──────────────────────────────────────────────────────────────────


def test_metrics_aggregate_by_class_and_status():
    kernel = ExecutionKernel()
    kernel.execute_sync(ExecutionRequest(argv=("true",), execution_class=ExecutionClass.SHELL))
    kernel.execute_sync(ExecutionRequest(argv=("false",), execution_class=ExecutionClass.SHELL))
    snap = kernel.metrics.snapshot()
    shell = snap["classes"]["shell"]
    assert shell["by_status"]["succeeded"] == 1
    assert shell["by_status"]["failed"] == 1
    assert shell["completed"] == 2
    assert shell["latency_avg_ms"] > 0


# ── Replay engine ────────────────────────────────────────────────────────────


def test_replay_reexecutes_audited_run():
    kernel = ExecutionKernel()
    replay = ReplayEngine(kernel, kernel.audit_engine)
    original = kernel.execute_sync(
        ExecutionRequest(argv=("echo", "replay-me"), execution_class=ExecutionClass.SHELL)
    )
    dry = replay.dry_run(original.execution_id)
    assert dry is not None
    assert dry["argv"] == ["echo", "replay-me"]
    rerun = replay.replay(original.execution_id)
    assert rerun is not None
    assert rerun.ok
    assert rerun.stdout == original.stdout
    assert rerun.execution_id != original.execution_id
    replayed_record = kernel.audit_engine.find(rerun.execution_id)
    assert replayed_record is not None


def test_replay_unknown_id_returns_none():
    kernel = ExecutionKernel()
    replay = ReplayEngine(kernel, kernel.audit_engine)
    assert replay.replay("does-not-exist") is None
    assert replay.dry_run("does-not-exist") is None


# ── Cancellation / supervisor ────────────────────────────────────────────────


def test_cancel_running_execution():
    import threading

    kernel = ExecutionKernel()
    request = ExecutionRequest(
        argv=("/bin/sh", "-c", "sleep 30"),
        execution_class=ExecutionClass.SHELL,
        limits=ResourceLimits(timeout=60, grace_period=1.0),
    )
    results: list = []
    t = threading.Thread(target=lambda: results.append(kernel.execute_sync(request)))
    t.start()
    time.sleep(0.5)
    assert kernel.cancel(request.execution_id, grace_period=1.0)
    t.join(timeout=10)
    assert results
    assert results[0].status is ExecutionStatus.CANCELLED


def test_shutdown_reconciles_orphans():
    kernel = ExecutionKernel()
    assert kernel.shutdown() == 0  # nothing tracked → nothing reconciled


# ── Spawn (long-running processes) ───────────────────────────────────────────


def test_spawn_and_graceful_termination():
    async def go():
        bus = EventBus()
        kernel = ExecutionKernel(event_bus=bus)
        handle = await kernel.spawn(
            ExecutionRequest(
                argv=("/bin/sh", "-c", "echo started; sleep 30"),
                execution_class=ExecutionClass.SHELL,
            )
        )
        line = await asyncio.wait_for(handle.stdout.readline(), timeout=5)
        assert line.decode().strip() == "started"
        assert handle.returncode is None
        await handle.terminate_gracefully(2.0)
        assert handle.returncode is not None
        # Let the watcher task record the exit.
        for _ in range(50):
            await asyncio.sleep(0.1)
            if kernel.supervisor.snapshot()["spawned_tracked"] == 0:
                break
        names = [e.event_type for e in bus.replay()]
        assert "ProcessSpawned" in names
        assert "ProcessExited" in names
        assert kernel.supervisor.snapshot()["spawned_tracked"] == 0

    asyncio.run(go())


def test_spawn_denied_by_policy_raises():
    async def go():
        kernel = ExecutionKernel()
        with pytest.raises(ExecutionDeniedError):
            await kernel.spawn(
                ExecutionRequest(argv=("rm", "x"), execution_class=ExecutionClass.GIT)
            )

    asyncio.run(go())


# ── Adapters ─────────────────────────────────────────────────────────────────


def test_shell_adapter_run():
    adapter = ShellAdapter(ExecutionKernel())
    result = adapter.run("echo shell-adapter && echo err >&2; exit 0")
    assert result.ok
    assert "shell-adapter" in result.stdout
    assert "err" in result.stderr


def test_git_adapter_run_capture_matches_old_contract(tmp_path):
    adapter = GitAdapter(ExecutionKernel())
    code, out = adapter.run_capture(tmp_path, "rev-parse", "--is-inside-work-tree")
    assert code != 0 or "true" in out  # tmp dir is not a repo → non-zero + message


def test_python_adapter_run_code():
    adapter = PythonAdapter(ExecutionKernel())
    result = adapter.run_code("print(6*7)")
    assert result.ok
    assert result.stdout.strip() == "42"
    assert result.execution_class is ExecutionClass.PYTHON


def test_docker_adapter_builds_runtime_argv():
    from deerflow.execution.testing import FakeExecutionKernel

    fake = FakeExecutionKernel(lambda r: (0, "", ""))
    adapter = DockerAdapter(fake, runtime="docker")
    adapter.ps("--filter", "name=x")
    assert fake.requests[0].argv[:2] == ("docker", "ps")
    adapter.stop("c1", stop_timeout=3)
    assert fake.requests[1].argv == ("docker", "stop", "-t", "3", "c1")
    with pytest.raises(ValueError):
        DockerAdapter(fake, runtime="podman")


def test_pm2_adapter_argv_and_policy():
    from deerflow.execution.testing import FakeExecutionKernel

    fake = FakeExecutionKernel(lambda r: (0, "", ""))
    adapter = Pm2Adapter(fake)
    adapter.restart("deerflow")
    assert fake.requests[0].argv == ("pm2", "restart", "deerflow")
    assert fake.requests[0].execution_class is ExecutionClass.PM2


def test_systemd_adapter_uses_noninteractive_sudo():
    from deerflow.execution.testing import FakeExecutionKernel

    fake = FakeExecutionKernel(lambda r: (0, "active\n", ""))
    adapter = SystemdAdapter(fake)
    assert adapter.is_active("cloudflared-nova.service") is True
    assert fake.requests[0].argv == (
        "sudo",
        "-n",
        "systemctl",
        "is-active",
        "cloudflared-nova.service",
    )


# ── DI container integration ─────────────────────────────────────────────────


def test_container_returns_singleton_kernel_and_override_works():
    from deerflow.execution.testing import FakeExecutionKernel
    from deerflow.services.container import service_container

    service_container.reset()
    try:
        k1 = service_container.execution_kernel()
        k2 = service_container.execution_kernel()
        assert k1 is k2
        assert isinstance(k1, ExecutionKernel)

        fake = FakeExecutionKernel()
        service_container.override(execution_kernel=fake)
        assert service_container.execution_kernel() is fake
    finally:
        service_container.reset()


def test_container_kernel_publishes_to_global_event_bus():
    from deerflow.events.bus import event_bus
    from deerflow.services.container import service_container

    service_container.reset()
    try:
        kernel = service_container.execution_kernel()
        before = event_bus.history_size
        kernel.execute_sync(
            ExecutionRequest(argv=("true",), execution_class=ExecutionClass.SHELL)
        )
        assert event_bus.history_size > before
    finally:
        service_container.reset()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells")
def test_kernel_never_uses_shell_true():
    """Structural guarantee: no call in the kernel passes shell=True."""
    import ast
    import inspect

    import deerflow.execution.kernel as kernel_mod

    tree = ast.parse(inspect.getsource(kernel_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    assert not (isinstance(kw.value, ast.Constant) and kw.value.value is True)
