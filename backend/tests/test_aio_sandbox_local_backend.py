import logging
import os
from types import SimpleNamespace

import pytest

from deerflow.community.aio_sandbox.local_backend import (
    LocalContainerBackend,
    _format_container_command_for_log,
    _format_container_mount,
    _redact_container_command_for_log,
    _resolve_docker_bind_host,
)
from deerflow.execution.testing import FakeExecutionKernel, timeout_result
from deerflow.services.container import service_container


@pytest.fixture(autouse=True)
def _isolated_service_container():
    """Each test gets a clean container; kernel fakes never leak across tests."""
    yield
    service_container.reset()


def _install_fake_kernel(handler) -> FakeExecutionKernel:
    fake = FakeExecutionKernel(handler)
    service_container.override(execution_kernel=fake)
    return fake


class TestDooDDaemonGuard:
    """aio/DooD daemon-reachability guard (2026-08-10 sandbox outage).

    The regression: the stack was started without the docker-compose.dood.yaml
    overlay, so the gateway had a working docker CLI but no socket — every
    sandbox tool call failed with a generic "Cannot connect to the Docker
    daemon". The probe and the create() guard turn that into an actionable
    error that names the exact fix.
    """

    def _backend(self) -> LocalContainerBackend:
        return LocalContainerBackend(
            image="sandbox:latest",
            base_port=8080,
            container_prefix="sandbox",
            config_mounts=[],
            environment={},
        )

    def test_probe_daemon_reports_reachable(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "docker")
        _install_fake_kernel(lambda request: (0, "29.6.2", ""))
        ok, detail = backend.probe_daemon()
        assert ok is True
        assert "29.6.2" in detail

    def test_probe_daemon_reports_unreachable(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "docker")
        _install_fake_kernel(lambda request: (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"))
        ok, detail = backend.probe_daemon()
        assert ok is False
        assert "Cannot connect to the Docker daemon" in detail

    def test_probe_daemon_container_runtime_skips_probe(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "container")
        ok, detail = backend.probe_daemon()
        assert ok is True
        assert "Apple Container" in detail

    def test_probe_daemon_issues_server_version_command(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "docker")
        fake = _install_fake_kernel(lambda request: (0, "29.6.2", ""))
        backend.probe_daemon()
        argv = fake.requests[0].argv
        assert argv[0] == "docker"
        assert "version" in argv
        assert "{{.Server.Version}}" in argv

    def test_create_raises_actionable_error_when_daemon_unreachable(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "docker")
        _install_fake_kernel(lambda request: (1, "", "Cannot connect to the Docker daemon"))
        with pytest.raises(RuntimeError, match="docker-compose.dood.yaml"):
            backend.create(thread_id="t1", sandbox_id="guard-abc")

    def test_create_proceeds_when_daemon_reachable(self, monkeypatch):
        backend = self._backend()
        monkeypatch.setattr(backend, "_runtime", "docker")

        def fake_exec(request):
            argv = list(request.argv)
            if "version" in argv:
                return (0, "29.6.2", "")
            if "run" in argv[:2]:
                return (0, "container-id\n", "")
            return (0, "", "")

        _install_fake_kernel(fake_exec)
        info = backend.create(thread_id="t1", sandbox_id="guard-ok")
        assert info.container_id == "container-id"


def test_format_container_mount_uses_mount_syntax_for_docker_windows_paths():
    args = _format_container_mount("docker", "D:/deer-flow/backend/.deer-flow/threads", "/mnt/threads", False)

    assert args == [
        "--mount",
        "type=bind,src=D:/deer-flow/backend/.deer-flow/threads,dst=/mnt/threads",
    ]


def test_format_container_mount_marks_docker_readonly_mounts():
    args = _format_container_mount("docker", "/host/path", "/mnt/path", True)

    assert args == [
        "--mount",
        "type=bind,src=/host/path,dst=/mnt/path,readonly",
    ]


def test_format_container_mount_keeps_volume_syntax_for_apple_container():
    args = _format_container_mount("container", "/host/path", "/mnt/path", True)

    assert args == [
        "-v",
        "/host/path:/mnt/path:ro",
    ]


def test_redact_container_command_for_log_redacts_env_values():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY=secret-value",
            "--env=TOKEN=token-value",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert "API_KEY=<redacted>" in redacted
    assert "--env=TOKEN=<redacted>" in redacted
    assert "secret-value" not in " ".join(redacted)
    assert "token-value" not in " ".join(redacted)


def test_redact_container_command_for_log_keeps_inherited_env_names():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY",
            "--env=TOKEN",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert redacted == [
        "docker",
        "run",
        "-e",
        "API_KEY",
        "--env=TOKEN",
        "--name",
        "sandbox",
        "image",
    ]


def test_format_container_command_for_log_uses_windows_quoting(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    command = _format_container_command_for_log(["docker", "run", "--name", "sandbox one", "image"])

    assert command == 'docker run --name "sandbox one" image'


def test_start_container_logs_redacted_env_values(monkeypatch, caplog):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={"API_KEY": "secret-value", "NORMAL": "visible-value"},
    )
    monkeypatch.setattr(backend, "_runtime", "docker")

    captured_cmd: list[str] = []

    def fake_exec(request):
        captured_cmd.extend(request.argv)
        return (0, "container-id\n", "")

    _install_fake_kernel(fake_exec)

    with caplog.at_level(logging.INFO, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._start_container("sandbox-test", 18080)

    joined_cmd = " ".join(captured_cmd)
    assert "API_KEY=secret-value" in joined_cmd
    assert "NORMAL=visible-value" in joined_cmd

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert "API_KEY=<redacted>" in log_output
    assert "NORMAL=<redacted>" in log_output
    assert "secret-value" not in log_output
    assert "visible-value" not in log_output


def _capture_start_container_command(monkeypatch, backend: LocalContainerBackend, runtime: str = "docker") -> list[str]:
    monkeypatch.setattr(backend, "_runtime", runtime)
    captured_cmd: list[str] = []

    def fake_exec(request):
        captured_cmd.extend(request.argv)
        return (0, "container-id\n", "")

    _install_fake_kernel(fake_exec)
    backend._start_container("sandbox-test", 18080)
    return captured_cmd


def test_resolve_docker_bind_host_defaults_loopback_for_localhost(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)

    assert _resolve_docker_bind_host() == "127.0.0.1"


def test_resolve_docker_bind_host_keeps_dood_compatibility(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")

    assert _resolve_docker_bind_host() == "0.0.0.0"


def test_resolve_docker_bind_host_uses_ipv6_loopback_for_ipv6_sandbox_host(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")

    assert _resolve_docker_bind_host() == "[::1]"


def test_resolve_docker_bind_host_logs_selected_bind_reason(caplog):
    with caplog.at_level(logging.DEBUG, logger="deerflow.community.aio_sandbox.local_backend"):
        assert _resolve_docker_bind_host(sandbox_host="localhost", bind_host="") == "127.0.0.1"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Docker sandbox bind: 127.0.0.1 (loopback default)" in messages


def test_resolve_docker_bind_host_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "localhost")
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "192.0.2.10")

    assert _resolve_docker_bind_host() == "192.0.2.10"


def test_start_container_binds_local_docker_port_to_loopback_by_default(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "127.0.0.1:18080:8080"


def test_start_container_keeps_broad_bind_for_dood_sandbox_host(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "0.0.0.0:18080:8080"


def test_start_container_binds_ipv6_sandbox_host_to_ipv6_loopback(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "[::1]:18080:8080"


def test_start_container_keeps_apple_container_port_format(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "127.0.0.1")

    captured_cmd = _capture_start_container_command(monkeypatch, backend, runtime="container")

    assert captured_cmd[captured_cmd.index("-p") + 1] == "18080:8080"


def _backend_for_inspect_tests() -> LocalContainerBackend:
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    backend._runtime = "docker"
    return backend


def test_is_container_running_false_when_container_missing(monkeypatch):
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: (1, "", "Error: No such object: sandbox-missing"))

    assert backend._is_container_running("sandbox-missing") is False


def test_is_container_running_raises_on_runtime_error(monkeypatch):
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: (1, "", "Cannot connect to the Docker daemon"))

    with pytest.raises(RuntimeError, match="Failed to inspect container sandbox-busy"):
        backend._is_container_running("sandbox-busy")


def test_is_container_running_raises_on_timeout(monkeypatch):
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: timeout_result(request, timeout=5))

    with pytest.raises(RuntimeError, match="Timed out checking container sandbox-timeout"):
        backend._is_container_running("sandbox-timeout")


def test_discover_returns_none_when_runtime_check_fails(monkeypatch):
    """A transient daemon error during discovery must fall through to create, not fail acquire."""
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: (1, "", "Cannot connect to the Docker daemon"))

    assert backend.discover("sandbox-blip") is None


def test_discover_returns_none_when_runtime_check_times_out(monkeypatch):
    """An inspect timeout during discovery must not propagate out of discover()."""
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: timeout_result(request, timeout=5))

    assert backend.discover("sandbox-timeout") is None


def test_is_container_running_false_on_apple_container_not_found(monkeypatch):
    """Apple Container's generic "not found" is trusted when it names the container."""
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: (1, "", 'Error: not found: "sandbox-apple"'))

    assert backend._is_container_running("sandbox-apple") is False


def test_is_container_running_raises_on_unrelated_not_found_error(monkeypatch):
    """Transient errors whose text contains "not found" must not be misread as a dead container."""
    backend = _backend_for_inspect_tests()

    _install_fake_kernel(lambda request: (1, "", "Error: credential helper not found in $PATH"))

    with pytest.raises(RuntimeError, match="Failed to inspect container sandbox-busy"):
        backend._is_container_running("sandbox-busy")


class TestPreviewPortCollisionQuarantine:
    """DooD preview-port collision (2026-07-18 incident).

    The gateway runs inside a container, so get_free_port's socket-bind check
    inspects the container's network namespace — a HOST process holding a
    preview port (the ops console on 4100) is invisible to it. Every
    `docker run` then failed with "failed to bind host port 0.0.0.0:4100/tcp:
    address already in use", and the create() retry loop only rotated the main
    port, reallocating 4100 forever. create() must learn from Docker's actual
    rejection: quarantine the rejected host port and retry with the next one.
    """

    def test_create_survives_host_preview_port_collision(self, monkeypatch):
        from deerflow.utils import network as network_module

        allocator = network_module._global_port_allocator
        reserved_before = set(allocator._reserved_ports)
        # Simulate the container namespace: every port looks bindable locally,
        # so only the reservation set (and Docker's rejections) can steer.
        monkeypatch.setattr(type(allocator), "_is_port_available", lambda self, port: port not in self._reserved_ports)

        backend = LocalContainerBackend(
            image="sandbox:latest",
            base_port=8080,
            container_prefix="sandbox",
            config_mounts=[],
            environment={},
            preview_container_ports=[4100, 4101, 4102],
        )
        monkeypatch.setattr(backend, "_runtime", "docker")

        def published_host_ports(argv: list[str]) -> list[str]:
            ports = []
            for i, arg in enumerate(argv):
                if arg == "-p" and i + 1 < len(argv):
                    parts = argv[i + 1].split(":")
                    if len(parts) >= 2:
                        ports.append(parts[-2])
            return ports

        attempts: list[list[str]] = []

        def fake_exec(request):
            argv = list(request.argv)
            if "run" in argv[:2]:
                attempts.append(argv)
                if "4100" in published_host_ports(argv):
                    return (
                        125,
                        "",
                        "docker: Error response from daemon: failed to set up container networking: "
                        "driver failed programming external connectivity on endpoint sandbox-cafe1234 (deadbeef): "
                        "failed to bind host port 0.0.0.0:4100/tcp: address already in use",
                    )
                return (0, "container-id\n", "")
            return (0, "", "")

        _install_fake_kernel(fake_exec)
        try:
            info = backend.create(thread_id="t1", sandbox_id="cafe1234")

            assert info.container_id == "container-id"
            # The collision was learned from Docker's rejection: exactly one
            # failed attempt, then success with 4100 remapped elsewhere.
            assert len(attempts) == 2
            assert "4100" in published_host_ports(attempts[0])
            assert "4100" not in published_host_ports(attempts[1])
            assert info.preview_ports[4100] != 4100
        finally:
            # Drop any reservations this test introduced (quarantined 4100 +
            # allocated ports) so the global allocator stays clean for others.
            allocator._reserved_ports.intersection_update(reserved_before)


# ── Resource caps and privileged mode ────────────────────────────────────────
#
# Sandbox containers ran with no memory limit, no pids limit and no way to ask
# for --privileged. The caps matter because an unbounded container on a host
# that already over-commits memory takes the whole machine down instead of just
# itself; --privileged matters because it is what the nested Docker daemon in
# the dind image layer needs, and it must never follow the image silently.


def test_start_container_applies_default_resource_caps(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        memory_limit="8g",
        pids_limit=2048,
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--memory" in cmd
    assert cmd[cmd.index("--memory") + 1] == "8g"
    assert "--pids-limit" in cmd
    assert cmd[cmd.index("--pids-limit") + 1] == "2048"


def test_start_container_omits_caps_when_unset(monkeypatch):
    """None means unlimited — the flag must be absent, not passed as "None"."""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        memory_limit=None,
        pids_limit=None,
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--memory" not in cmd
    assert "--pids-limit" not in cmd


def test_start_container_is_unprivileged_by_default(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )

    assert "--privileged" not in _capture_start_container_command(monkeypatch, backend)


def test_start_container_adds_privileged_when_enabled(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        privileged=True,
    )

    assert "--privileged" in _capture_start_container_command(monkeypatch, backend)


def test_start_container_sets_shm_size(monkeypatch):
    """Docker's 64 MB /dev/shm default hangs Chromium mid-render.

    The failure is not an error but a timeout: the page loads, then screenshot
    or renderer work blocks. The sandbox image carries a browser stack and
    Playwright, so this is the agent's own browsing.
    """
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        shm_size="1g",
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--shm-size" in cmd
    assert cmd[cmd.index("--shm-size") + 1] == "1g"


def test_start_container_omits_shm_size_when_unset(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        shm_size=None,
    )

    assert "--shm-size" not in _capture_start_container_command(monkeypatch, backend)


def test_start_container_sets_cpu_shares_by_default(monkeypatch):
    """Shares, not a quota. --cpus deschedules the container once its quota is
    spent even on an idle machine, which on long agent work reads as a stall.
    Shares only bind under real contention."""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        cpu_shares=512,
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--cpu-shares" in cmd
    assert cmd[cmd.index("--cpu-shares") + 1] == "512"
    assert "--cpus" not in cmd


def test_start_container_applies_hard_cpu_quota_only_when_asked(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        cpu_limit="4",
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--cpus" in cmd
    assert cmd[cmd.index("--cpus") + 1] == "4"


def test_start_container_omits_cpu_flags_when_unset(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )

    cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--cpus" not in cmd
    assert "--cpu-shares" not in cmd


# ── stale-name recovery ──────────────────────────────────────────────────────
#
# A create that is interrupted before start leaves a container in state
# "Created" holding the deterministic per-thread name. `docker ps` without -a
# cannot see it, so discover() finds nothing to adopt and every later turn for
# that thread fails identically -- "the container name is already in use" --
# with no path out except a human running `docker rm` on the host. This wedged
# a live thread on 2026-08-22.


def test_remove_stale_container_removes_a_non_running_holder(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    calls: list[list[str]] = []

    def fake_cli(*args, **kwargs):
        calls.append(list(args))
        if args[0] == "inspect":
            return SimpleNamespace(ok=True, stdout="false\n", stderr="", error=None, exit_code=0)
        return SimpleNamespace(ok=True, stdout="", stderr="", error=None, exit_code=0)

    monkeypatch.setattr(backend, "_cli", fake_cli)

    assert backend._remove_stale_container("sandbox-abc") is True
    assert ["rm", "-f", "sandbox-abc"] in calls, "the stale container must actually be removed"


def test_remove_stale_container_refuses_to_touch_a_running_one(monkeypatch):
    """Stealing a live sandbox's name mid-run is worse than failing the create."""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    calls: list[list[str]] = []

    def fake_cli(*args, **kwargs):
        calls.append(list(args))
        if args[0] == "inspect":
            return SimpleNamespace(ok=True, stdout="true\n", stderr="", error=None, exit_code=0)
        return SimpleNamespace(ok=True, stdout="", stderr="", error=None, exit_code=0)

    monkeypatch.setattr(backend, "_cli", fake_cli)

    assert backend._remove_stale_container("sandbox-live") is False
    assert not any(c[:2] == ["rm", "-f"] for c in calls), "a running container must never be removed"
