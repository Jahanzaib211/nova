"""Docker-backed sandbox container lifecycle and cleanup tests.

This test module requires Docker to be running. It exercises the container
backend behavior behind sandbox lifecycle management and verifies that test
containers are created, observed, and explicitly cleaned up correctly.

The coverage here is limited to direct backend/container operations used by
the reconciliation flow. It does not simulate a process restart by creating
a new ``AioSandboxProvider`` instance or assert provider startup orphan
reconciliation end-to-end — that logic is covered by unit tests in
``test_sandbox_orphan_reconciliation.py``.

Run with: PYTHONPATH=. uv run pytest tests/test_sandbox_orphan_reconciliation_e2e.py -v -s
Requires: Docker running locally
"""

import subprocess
import time

import pytest

# One timeout for every `docker` call in this file, generous on purpose.
#
# These are real daemon round-trips, and under full-suite load the daemon is
# contended: this file failed `test_multiple_orphans_all_cleaned` on one run and
# `test_list_running_ignores_unrelated_containers` on the next, while each
# passed in isolation. The bug was never in the code under test — 15s was
# simply not enough for `docker stop` on a busy host. A flaky suite teaches
# people to re-run rather than read, which is how a real failure gets ignored.
_DOCKER_TIMEOUT_S = 90


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_TIMEOUT_S)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _container_running(container_name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True,
        text=True,
        timeout=_DOCKER_TIMEOUT_S,
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _wait_until_stopped(container_name: str, timeout_s: float = 30.0) -> bool:
    """Poll until the container is no longer running, or give up.

    Replaces `time.sleep(1); assert not _container_running(...)`. That fixed
    sleep is a claim about how quickly `docker stop` completes, and on a host
    running the rest of the suite it is simply wrong -- which is why this file
    failed a different test on each of two consecutive full runs while every
    test passed in isolation. Polling is not slower in the common case: it
    returns as soon as the container is actually gone.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _container_running(container_name):
            return True
        time.sleep(0.1)
    return not _container_running(container_name)


def _stop_container(container_name: str) -> None:
    subprocess.run(["docker", "stop", container_name], capture_output=True, timeout=_DOCKER_TIMEOUT_S)


# Use a lightweight image for testing to avoid pulling the heavy sandbox image
E2E_TEST_IMAGE = "busybox:latest"
E2E_PREFIX = "deer-flow-sandbox-e2e-test"


@pytest.fixture(autouse=True)
def cleanup_test_containers():
    """Ensure all test containers are cleaned up after the test."""
    yield
    # Cleanup: stop any remaining test containers
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={E2E_PREFIX}-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=_DOCKER_TIMEOUT_S,
    )
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=_DOCKER_TIMEOUT_S)


@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
class TestOrphanReconciliationE2E:
    """E2E tests for orphan container reconciliation."""

    def test_orphan_container_destroyed_on_startup(self):
        """Core issue scenario: container from a previous process is destroyed on new process init.

        Steps:
        1. Start a container manually (simulating previous process)
        2. Create a LocalContainerBackend with matching prefix
        3. Call list_running() → should find the container
        4. Simulate _reconcile_orphans() logic → container should be destroyed
        """
        container_name = f"{E2E_PREFIX}-orphan01"

        # Step 1: Start a container (simulating previous process lifecycle)
        result = subprocess.run(
            ["docker", "run", "--rm", "-d", "--name", container_name, E2E_TEST_IMAGE, "sleep", "3600"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
        )
        assert result.returncode == 0, f"Failed to start test container: {result.stderr}"

        try:
            assert _container_running(container_name), "Test container should be running"

            # Step 2: Create backend and list running containers
            from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend

            backend = LocalContainerBackend(
                image=E2E_TEST_IMAGE,
                base_port=9990,
                container_prefix=E2E_PREFIX,
                config_mounts=[],
                environment={},
            )

            # Step 3: list_running should find our container
            running = backend.list_running()
            found_ids = {info.sandbox_id for info in running}
            assert "orphan01" in found_ids, f"Should find orphan01, got: {found_ids}"

            # Step 4: Simulate reconciliation — this container's created_at is recent,
            # so with a very short idle_timeout it would be destroyed
            orphan_info = next(info for info in running if info.sandbox_id == "orphan01")
            assert orphan_info.created_at > 0, "created_at should be parsed from docker inspect"

            # Destroy it (simulating what _reconcile_orphans does for old containers)
            backend.destroy(orphan_info)

            assert _wait_until_stopped(container_name), "Orphan container should be stopped after destroy"

        finally:
            # Safety cleanup
            _stop_container(container_name)

    def test_multiple_orphans_all_cleaned(self):
        """Multiple orphaned containers are all found and can be cleaned up."""
        containers = []
        try:
            # Start 3 containers
            for i in range(3):
                name = f"{E2E_PREFIX}-multi{i:02d}"
                result = subprocess.run(
                    ["docker", "run", "--rm", "-d", "--name", name, E2E_TEST_IMAGE, "sleep", "3600"],
                    capture_output=True,
                    text=True,
                    timeout=_DOCKER_TIMEOUT_S,
                )
                assert result.returncode == 0, f"Failed to start {name}: {result.stderr}"
                containers.append(name)

            from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend

            backend = LocalContainerBackend(
                image=E2E_TEST_IMAGE,
                base_port=9990,
                container_prefix=E2E_PREFIX,
                config_mounts=[],
                environment={},
            )

            running = backend.list_running()
            found_ids = {info.sandbox_id for info in running}

            assert "multi00" in found_ids
            assert "multi01" in found_ids
            assert "multi02" in found_ids

            # Destroy all
            for info in running:
                backend.destroy(info)

            # Verify all gone
            for name in containers:
                assert _wait_until_stopped(name), f"{name} should be stopped"

        finally:
            for name in containers:
                _stop_container(name)

    def test_list_running_ignores_unrelated_containers(self):
        """Containers with different prefixes should not be listed."""
        unrelated_name = "unrelated-test-container"
        our_name = f"{E2E_PREFIX}-ours001"

        try:
            # Start an unrelated container
            subprocess.run(
                ["docker", "run", "--rm", "-d", "--name", unrelated_name, E2E_TEST_IMAGE, "sleep", "3600"],
                capture_output=True,
                timeout=_DOCKER_TIMEOUT_S,
            )
            # Start our container
            subprocess.run(
                ["docker", "run", "--rm", "-d", "--name", our_name, E2E_TEST_IMAGE, "sleep", "3600"],
                capture_output=True,
                timeout=_DOCKER_TIMEOUT_S,
            )

            from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend

            backend = LocalContainerBackend(
                image=E2E_TEST_IMAGE,
                base_port=9990,
                container_prefix=E2E_PREFIX,
                config_mounts=[],
                environment={},
            )

            running = backend.list_running()
            found_ids = {info.sandbox_id for info in running}

            # Should find ours but not unrelated
            assert "ours001" in found_ids
            # "unrelated-test-container" doesn't match "deer-flow-sandbox-e2e-test-" prefix
            for info in running:
                assert not info.sandbox_id.startswith("unrelated")

        finally:
            _stop_container(unrelated_name)
            _stop_container(our_name)
