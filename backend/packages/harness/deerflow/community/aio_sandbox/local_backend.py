"""Local container backend for sandbox provisioning.

Manages sandbox containers using Docker or Apple Container on the local machine.
Handles container lifecycle, port allocation, and cross-process container discovery.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from datetime import datetime

from deerflow.execution.models import ExecutionResult, ExecutionStatus
from deerflow.utils.network import get_free_port, release_port, reserve_port

from .backend import SandboxBackend, wait_for_sandbox_ready
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def _parse_docker_timestamp(raw: str) -> float:
    """Parse Docker's ISO 8601 timestamp into a Unix epoch float.

    Docker returns timestamps with nanosecond precision and a trailing ``Z``
    (e.g. ``2026-04-08T01:22:50.123456789Z``).  Python's ``fromisoformat``
    accepts at most microseconds and (pre-3.11) does not accept ``Z``, so the
    string is normalized before parsing.  Returns ``0.0`` on empty input or
    parse failure so callers can use ``0.0`` as a sentinel for "unknown age".
    """
    if not raw:
        return 0.0
    try:
        s = raw.strip()
        if "." in s:
            dot_pos = s.index(".")
            tz_start = dot_pos + 1
            while tz_start < len(s) and s[tz_start].isdigit():
                tz_start += 1
            frac = s[dot_pos + 1 : tz_start][:6]  # truncate to microseconds
            tz_suffix = s[tz_start:]
            s = s[: dot_pos + 1] + frac + tz_suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not parse docker timestamp {raw!r}: {e}")
        return 0.0


def _extract_host_port(inspect_entry: dict, container_port: int) -> int | None:
    """Extract the host port mapped to ``container_port/tcp`` from a docker inspect entry.

    Returns None if the container has no port mapping for that port.
    """
    try:
        ports = (inspect_entry.get("NetworkSettings") or {}).get("Ports") or {}
        bindings = ports.get(f"{container_port}/tcp") or []
        if bindings:
            host_port = bindings[0].get("HostPort")
            if host_port:
                return int(host_port)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _format_container_mount(runtime: str, host_path: str, container_path: str, read_only: bool) -> list[str]:
    """Format a bind-mount argument for the selected runtime.

    Docker's ``-v host:container`` syntax is ambiguous for Windows drive-letter
    paths like ``D:/...`` because ``:`` is both the drive separator and the
    volume separator. Use ``--mount type=bind,...`` for Docker to avoid that
    parsing ambiguity. Apple Container keeps using ``-v``.
    """
    if runtime == "docker":
        mount_spec = f"type=bind,src={host_path},dst={container_path}"
        if read_only:
            mount_spec += ",readonly"
        return ["--mount", mount_spec]

    mount_spec = f"{host_path}:{container_path}"
    if read_only:
        mount_spec += ":ro"
    return ["-v", mount_spec]


def _redact_container_command_for_log(cmd: list[str]) -> list[str]:
    """Return a Docker/Container command with environment values redacted."""
    redacted: list[str] = []
    redact_next_env = False

    for arg in cmd:
        if redact_next_env:
            if "=" in arg:
                key = arg.split("=", 1)[0]
                redacted.append(f"{key}=<redacted>" if key else "<redacted>")
            else:
                redacted.append(arg)
            redact_next_env = False
            continue

        if arg in {"-e", "--env"}:
            redacted.append(arg)
            redact_next_env = True
            continue

        if arg.startswith("--env="):
            value = arg.removeprefix("--env=")
            if "=" in value:
                key = value.split("=", 1)[0]
                redacted.append(f"--env={key}=<redacted>" if key else "--env=<redacted>")
            else:
                redacted.append(arg)
            continue

        redacted.append(arg)

    return redacted


def _format_container_command_for_log(cmd: list[str]) -> str:
    if os.name == "nt":
        # Formatting only — no execution (Phase C7: execution lives in the kernel).
        from subprocess import list2cmdline

        return list2cmdline(cmd)
    return shlex.join(cmd)


def _normalize_sandbox_host(host: str) -> str:
    return host.strip().lower()


def _is_ipv6_loopback_sandbox_host(host: str) -> bool:
    return _normalize_sandbox_host(host) in {"::1", "[::1]"}


def _is_loopback_sandbox_host(host: str) -> bool:
    return _normalize_sandbox_host(host) in {"", "localhost", "127.0.0.1", "::1", "[::1]"}


def _resolve_docker_bind_host(sandbox_host: str | None = None, bind_host: str | None = None) -> str:
    """Choose the host interface for legacy Docker ``-p`` sandbox publishing.

    Bare-metal/local runs talk to sandboxes through localhost and should not
    expose the sandbox HTTP API on every host interface.  Docker-outside-of-
    Docker deployments commonly use ``host.docker.internal`` from another
    container; keep their legacy broad bind unless operators opt into a
    narrower bind with ``DEER_FLOW_SANDBOX_BIND_HOST``.  When operators choose
    an IPv6 loopback sandbox host, bind Docker to IPv6 loopback as well so the
    advertised sandbox URL and published socket use the same address family.
    """
    explicit_bind = bind_host if bind_host is not None else os.environ.get("DEER_FLOW_SANDBOX_BIND_HOST")
    if explicit_bind is not None:
        explicit_bind = explicit_bind.strip()
        if explicit_bind:
            logger.debug("Docker sandbox bind: %s (explicit bind host override)", explicit_bind)
            return explicit_bind

    host = sandbox_host if sandbox_host is not None else os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
    if _is_ipv6_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: [::1] (IPv6 loopback sandbox host)")
        return "[::1]"
    if _is_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: 127.0.0.1 (loopback default)")
        return "127.0.0.1"

    logger.debug("Docker sandbox bind: 0.0.0.0 (non-loopback sandbox host compatibility)")
    return "0.0.0.0"


def _is_no_such_container_error(stderr: str, container_name: str) -> bool:
    """Return True only when stderr definitively says the container does not exist.

    Docker reports "No such object" / "No such container". Apple Container
    reports a generic "not found", so that phrase is only trusted when the
    message also names the inspected container (or refers to a
    container/object); transient failures whose text happens to contain
    "not found" (e.g. "command not found", "context not found") must stay on
    the raise path instead of being misread as a dead container.
    """
    message = stderr.lower()
    if "no such object" in message or "no such container" in message:
        return True
    if "not found" not in message:
        return False
    return container_name.lower() in message or "container" in message or "object" in message


class LocalContainerBackend(SandboxBackend):
    """Backend that manages sandbox containers locally using Docker or Apple Container.

    On macOS, automatically prefers Apple Container if available, otherwise falls back to Docker.
    On other platforms, uses Docker.

    Features:
    - Deterministic container naming for cross-process discovery
    - Port allocation with thread-safe utilities
    - Container lifecycle management (start/stop with --rm)
    - Support for volume mounts and environment variables
    """

    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        config_mounts: list,
        environment: dict[str, str],
        preview_container_ports: list[int] | None = None,
        privileged: bool = False,
        memory_limit: str | None = None,
        pids_limit: int | None = None,
        shm_size: str | None = None,
    ):
        """Initialize the local container backend.

        Args:
            image: Container image to use.
            base_port: Base port number to start searching for free ports.
            container_prefix: Prefix for container names (e.g., "deer-flow-sandbox").
            config_mounts: Volume mount configurations from config (list of VolumeMountConfig).
            environment: Environment variables to inject into containers.
            preview_container_ports: Extra container ports to publish for the
                in-container dev-server preview (e.g. [4100, 4101, 4102]). Each
                gets mapped to a free host port so the gateway can HTTP-proxy a
                dev server running inside the sandbox. Empty disables preview
                publishing.
            privileged: Run containers with --privileged, which the nested Docker
                daemon in the dind image layer requires. Grants effective host
                root to anything that escapes the sandbox.
            memory_limit: Value for --memory (e.g. "8g"); None for unlimited.
            pids_limit: Value for --pids-limit; None for unlimited.
            shm_size: Value for --shm-size; None for the Docker default (64 MB).
        """
        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._config_mounts = config_mounts
        self._environment = environment
        self._preview_container_ports = list(preview_container_ports or [])
        self._privileged = privileged
        self._memory_limit = memory_limit
        self._pids_limit = pids_limit
        self._shm_size = shm_size
        self._runtime = self._detect_runtime()

    @property
    def runtime(self) -> str:
        """The detected container runtime ("docker" or "container")."""
        return self._runtime

    def _detect_runtime(self) -> str:
        """Detect which container runtime to use.

        On macOS, prefer Apple Container if available, otherwise fall back to Docker.
        On other platforms, use Docker.

        Returns:
            "container" for Apple Container, "docker" for Docker.
        """
        import platform

        if platform.system() == "Darwin":
            from deerflow.execution.adapters import DockerAdapter
            from deerflow.services.container import service_container

            probe = DockerAdapter(service_container.execution_kernel(), runtime="container")
            result = probe.version(timeout=5)
            if result.ok:
                logger.info(f"Detected Apple Container: {result.stdout.strip()}")
                return "container"
            logger.info("Apple Container not available, falling back to Docker")

        return "docker"

    def _cli(self, *args: str, timeout: float = 30.0, intent: str = "") -> ExecutionResult:
        """Run a container-runtime CLI command through the Execution Kernel.

        The adapter is rebuilt per call so ``self._runtime`` changes (tests,
        late detection) always take effect.
        """
        from deerflow.execution.adapters import DockerAdapter
        from deerflow.services.container import service_container

        adapter = DockerAdapter(service_container.execution_kernel(), runtime=self._runtime)
        return adapter.cli(*args, timeout=timeout, intent=intent)

    def probe_daemon(self) -> tuple[bool, str]:
        """Return ``(reachable, detail)`` for the container runtime daemon.

        In pure DooD mode the gateway reaches the host daemon through the
        Docker socket mounted by the docker-compose.dood.yaml overlay. When the
        stack is started without that overlay the CLI exists inside the gateway
        but the daemon is unreachable, and every later ``docker run`` fails
        with a generic "Cannot connect to the Docker daemon" — the failure mode
        of the 2026-08-10 sandbox outage. Probing up front lets create() fail
        with an actionable error instead of a confusing generic one.

        Apple Container needs no daemon handshake, so it always reports ready.
        """
        if self._runtime == "container":
            return True, "Apple Container (no daemon handshake required)"
        result = self._cli(
            "version",
            "--format",
            "{{.Server.Version}}",
            timeout=10.0,
            intent="docker daemon reachability probe",
        )
        if result.ok:
            return True, f"Docker {result.stdout.strip()}"
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "docker daemon unreachable"

    # ── SandboxBackend interface ──────────────────────────────────────────

    def create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """Start a new container and return its connection info.

        Args:
            thread_id: Thread ID for which the sandbox is being created. Useful for backends that want to organize sandboxes by thread.
            sandbox_id: Deterministic sandbox identifier (used in container name).
            extra_mounts: Additional volume mounts as (host_path, container_path, read_only) tuples.

        Returns:
            SandboxInfo with container details.

        Raises:
            RuntimeError: If the container fails to start.
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        # DooD guard: in aio mode the gateway talks to the host daemon through
        # the socket mounted by docker-compose.dood.yaml. If that overlay was
        # dropped at stack start, the CLI answers but the daemon does not, and
        # every sandbox tool call dies with a generic daemon error. Fail here
        # with the exact remediation instead.
        if self._runtime == "docker":
            daemon_ok, daemon_detail = self.probe_daemon()
            if not daemon_ok:
                logger.error(
                    "Sandbox daemon unreachable (%s) — aio/DooD mode needs the host Docker socket mounted into the gateway",
                    daemon_detail,
                )
                raise RuntimeError(
                    "Cannot start sandbox: the Docker daemon is unreachable from the "
                    f"gateway ({daemon_detail}). In aio (DooD) mode the host Docker "
                    "socket must be mounted into the gateway. Restart the stack with "
                    "`scripts/docker.sh start` (it appends docker-compose.dood.yaml "
                    "when aio mode is detected) or add `-f docker-compose.dood.yaml` "
                    "to the compose command."
                )

        def _rejected_host_port(error_text: str) -> int | None:
            """Extract the host port Docker refused to bind, if named.

            Matches both daemon error shapes:
            - "failed to bind host port 0.0.0.0:4100/tcp: address already in use"
            - "Bind for 0.0.0.0:4100 failed: port is already allocated"
            """
            match = re.search(r"bind (?:host port )?(?:for )?[\d.:\[\]]*:(\d+)(?:/tcp)?", error_text, re.IGNORECASE)
            return int(match.group(1)) if match else None

        # Retry loop: if Docker rejects a port, learn from the rejection and
        # try again. Two distinct cases:
        # - main API port rejected -> rotate to the next candidate.
        # - a PREVIEW host port rejected -> quarantine it via reserve_port so
        #   the next attempt allocates a different one. This is the only
        #   reliable signal in DooD setups: the socket-bind check in
        #   get_free_port inspects THIS process's network namespace, so a
        #   host-side listener (e.g. another product on :4100) is invisible
        #   to it and every naive retry would re-pick the same colliding port
        #   (2026-07-18 sandbox outage).
        _next_start = self._base_port
        container_id: str | None = None
        port: int = 0
        preview_ports: dict[int, int] = {}
        for _attempt in range(10):
            port = get_free_port(start_port=_next_start)
            try:
                container_id, preview_ports = self._start_container(container_name, port, extra_mounts)
                break
            except RuntimeError as exc:
                release_port(port)
                err = str(exc)
                err_lower = err.lower()
                # Port already bound: learn which port Docker refused.
                if "port is already allocated" in err or "address already in use" in err_lower:
                    rejected = _rejected_host_port(err)
                    if rejected is not None and rejected != port:
                        # A preview host port collided with a host-side
                        # listener this process cannot see. Quarantine it for
                        # the process lifetime and retry; the main port was
                        # fine, so don't rotate it.
                        reserve_port(rejected)
                        logger.warning(f"Preview host port {rejected} rejected by Docker (address already in use on the host); quarantined for this process, retrying with a fresh port")
                        continue
                    logger.warning(f"Port {port} rejected by Docker (already allocated), retrying with next port")
                    _next_start = port + 1
                    continue
                # Container-name conflict: another process may have already started
                # the deterministic sandbox container for this sandbox_id. Try to
                # discover and adopt the existing container instead of failing.
                if "is already in use by container" in err_lower or "conflict. the container name" in err_lower:
                    logger.warning(f"Container name {container_name} already in use, attempting to discover existing sandbox instance")
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
                raise
        else:
            raise RuntimeError("Could not start sandbox container: all candidate ports are already allocated by Docker")

        # When running inside Docker (DooD), sandbox containers are reachable via
        # host.docker.internal rather than localhost (they run on the host daemon).
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{sandbox_host}:{port}",
            container_name=container_name,
            container_id=container_id,
            preview_ports=preview_ports,
        )

    def destroy(self, info: SandboxInfo) -> None:
        """Stop the container and release its port."""
        # Prefer container_id, fall back to container_name (both accepted by docker stop).
        # This ensures containers discovered via list_running() (which only has the name)
        # can also be stopped.
        stop_target = info.container_id or info.container_name
        if stop_target:
            self._stop_container(stop_target)
        # Extract port from sandbox_url for release
        try:
            from urllib.parse import urlparse

            port = urlparse(info.sandbox_url).port
            if port:
                release_port(port)
        except Exception:
            pass
        # Release any published preview host ports too.
        for host_preview_port in (info.preview_ports or {}).values():
            try:
                release_port(host_preview_port)
            except Exception:
                pass

    def is_alive(self, info: SandboxInfo) -> bool:
        """Check if the container is still running (lightweight, no HTTP)."""
        if info.container_name:
            return self._is_container_running(info.container_name)
        return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """Discover an existing container by its deterministic name.

        Checks if a container with the expected name is running, retrieves its
        port, and verifies it responds to health checks.

        Args:
            sandbox_id: The deterministic sandbox ID (determines container name).

        Returns:
            SandboxInfo if container found and healthy, None otherwise. A
            failed runtime check (e.g. transient daemon error) also returns
            None — discovery must not adopt a container it cannot verify, and
            falling through to create keeps acquire recoverable instead of
            hard-failing on a hiccup.
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        def _rejected_host_port(error_text: str) -> int | None:
            """Extract the host port Docker refused to bind, if named.

            Matches both daemon error shapes:
            - "failed to bind host port 0.0.0.0:4100/tcp: address already in use"
            - "Bind for 0.0.0.0:4100 failed: port is already allocated"
            """
            match = re.search(r"bind (?:host port )?(?:for )?[\d.:\[\]]*:(\d+)(?:/tcp)?", error_text, re.IGNORECASE)
            return int(match.group(1)) if match else None

        try:
            running = self._is_container_running(container_name)
        except RuntimeError as e:
            logger.warning(f"Could not verify container {container_name} during discovery; not adopting it: {e}")
            return None

        if not running:
            return None

        port = self._get_container_port(container_name)
        if port is None:
            return None

        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            return None

        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
            preview_ports=self._get_container_preview_ports(container_name),
        )

    def list_running(self) -> list[SandboxInfo]:
        """Enumerate all running containers matching the configured prefix.

        Uses a single ``docker ps`` call to list container names, then a
        single batched ``docker inspect`` call to retrieve creation timestamp
        and port mapping for all containers at once.  Total subprocess calls:
        2 (down from 2N+1 in the naive per-container approach).

        Note: Docker's ``--filter name=`` performs *substring* matching,
        so a secondary ``startswith`` check is applied to ensure only
        containers with the exact prefix are included.

        Containers without port mappings are still included (with empty
        sandbox_url) so that startup reconciliation can adopt orphans
        regardless of their port state.
        """
        # Step 1: enumerate container names via docker ps
        result = self._cli(
            "ps",
            "--filter",
            f"name={self._container_prefix}-",
            "--format",
            "{{.Names}}",
            timeout=10,
            intent="list running sandbox containers",
        )
        if not result.ok:
            stderr = (result.stderr or result.error or "").strip()
            logger.warning(
                "Failed to list running containers with %s ps (exit_code=%s, stderr=%s)",
                self._runtime,
                result.exit_code,
                stderr or "<empty>",
            )
            return []
        if not result.stdout.strip():
            return []

        # Filter to names matching our exact prefix (docker filter is substring-based)
        container_names = [name.strip() for name in result.stdout.strip().splitlines() if name.strip().startswith(self._container_prefix + "-")]
        if not container_names:
            return []

        # Step 2: batched docker inspect — single subprocess call for all containers
        inspections = self._batch_inspect(container_names)

        infos: list[SandboxInfo] = []
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        for container_name in container_names:
            data = inspections.get(container_name)
            if data is None:
                # Container disappeared between ps and inspect, or inspect failed
                continue
            created_at, host_port, preview_ports = data
            sandbox_id = container_name[len(self._container_prefix) + 1 :]
            sandbox_url = f"http://{sandbox_host}:{host_port}" if host_port else ""

            infos.append(
                SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=sandbox_url,
                    container_name=container_name,
                    created_at=created_at,
                    preview_ports=preview_ports,
                )
            )

        logger.info(f"Found {len(infos)} running sandbox container(s)")
        return infos

    def _batch_inspect(self, container_names: list[str]) -> dict[str, tuple[float, int | None, dict[int, int]]]:
        """Batch-inspect containers in a single subprocess call.

        Returns a mapping of ``container_name -> (created_at, host_port, preview_ports)``
        so reconciled/adopted containers keep their published preview ports after
        a gateway restart. Missing containers or parse failures are silently
        dropped from the result.
        """
        if not container_names:
            return {}
        result = self._cli(
            "inspect",
            *container_names,
            timeout=15,
            intent=f"batch-inspect {len(container_names)} sandbox containers",
        )
        if not result.ok:
            stderr = (result.stderr or result.error or "").strip()
            logger.warning(
                "Failed to batch-inspect containers with %s inspect (exit_code=%s, stderr=%s)",
                self._runtime,
                result.exit_code,
                stderr or "<empty>",
            )
            return {}

        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse docker inspect output as JSON: {e}")
            return {}

        out: dict[str, tuple[float, int | None, dict[int, int]]] = {}
        for entry in payload:
            # ``Name`` is prefixed with ``/`` in the docker inspect response
            name = (entry.get("Name") or "").lstrip("/")
            if not name:
                continue
            created_at = _parse_docker_timestamp(entry.get("Created", ""))
            host_port = _extract_host_port(entry, 8080)
            preview_ports: dict[int, int] = {}
            for container_preview_port in self._preview_container_ports:
                mapped = _extract_host_port(entry, container_preview_port)
                if mapped:
                    preview_ports[container_preview_port] = mapped
            out[name] = (created_at, host_port, preview_ports)
        return out

    # ── Container operations ─────────────────────────────────────────────

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> tuple[str, dict[int, int]]:
        """Start a new container.

        Args:
            container_name: Name for the container.
            port: Host port to map to container port 8080.
            extra_mounts: Additional volume mounts.

        Returns:
            A tuple of (container ID, preview_ports) where preview_ports maps
            each published container preview port to its host port.

        Raises:
            RuntimeError: If container fails to start.
        """
        cmd = [self._runtime, "run"]

        # Resource caps. Sandboxes ran with no limit at all until 2026-08-21,
        # on a host whose Committed_AS already exceeded its CommitLimit — so a
        # single runaway build took the machine down instead of just its own
        # container. These bound the blast radius, and matter more now that the
        # dind layer lets the sandbox start containers of its own.
        if self._memory_limit:
            cmd.extend(["--memory", str(self._memory_limit)])
        if self._pids_limit:
            cmd.extend(["--pids-limit", str(self._pids_limit)])
        # Docker defaults /dev/shm to 64 MB. Chromium treats that as fatal in a
        # way that looks like a hang rather than an error: the page loads, and
        # then screenshot/renderer work blocks until it times out. The sandbox
        # image carries both a browser stack and Playwright, so this is the
        # agent's own browsing, not just test tooling.
        if self._shm_size:
            cmd.extend(["--shm-size", str(self._shm_size)])

        # Docker-specific security options
        if self._runtime == "docker":
            cmd.extend(["--security-opt", "seccomp=unconfined"])
            # --privileged is what makes the dind layer's nested daemon work.
            # It is effectively host root, so it stays behind an explicit
            # config flag that defaults to off rather than following the image.
            if self._privileged:
                cmd.append("--privileged")
            # Make host services reachable from inside the sandbox at
            # host.docker.internal (Linux needs the explicit host-gateway
            # mapping; Docker Desktop provides it natively). Without this,
            # a user's dev server on the host (e.g. python -m http.server
            # 8765) is unreachable and in-sandbox browser navigation to it
            # dead-ends. Podman resolves host.containers.internal natively,
            # so this stays docker-only.
            cmd.extend(["--add-host", "host.docker.internal:host-gateway"])

        bind_host = _resolve_docker_bind_host()
        if self._runtime == "docker":
            port_mapping = f"{bind_host}:{port}:8080"
        else:
            port_mapping = f"{port}:8080"

        cmd.extend(
            [
                "--rm",
                "-d",
                "-p",
                port_mapping,
                "--name",
                container_name,
            ]
        )

        # Publish extra ports for the in-container dev-server preview. Each
        # container preview port (e.g. 4100) is mapped to a free host port so
        # the gateway can HTTP-proxy a dev server running inside the sandbox.
        preview_ports: dict[int, int] = {}
        for container_preview_port in self._preview_container_ports:
            # Wide search range so concurrent containers don't exhaust the pool
            # (each publishes one host port per preview port). A Docker rejection
            # of any published port fails the whole `docker run`, which the
            # create() retry loop catches and reallocates the full set.
            host_preview_port = get_free_port(start_port=container_preview_port, max_range=400)
            preview_ports[container_preview_port] = host_preview_port
            if self._runtime == "docker":
                preview_mapping = f"{bind_host}:{host_preview_port}:{container_preview_port}"
            else:
                preview_mapping = f"{host_preview_port}:{container_preview_port}"
            cmd.extend(["-p", preview_mapping])

        # Environment variables
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Config-level volume mounts
        for mount in self._config_mounts:
            cmd.extend(
                _format_container_mount(
                    self._runtime,
                    mount.host_path,
                    mount.container_path,
                    mount.read_only,
                )
            )

        # Extra mounts (thread-specific, skills, etc.)
        if extra_mounts:
            for host_path, container_path, read_only in extra_mounts:
                cmd.extend(
                    _format_container_mount(
                        self._runtime,
                        host_path,
                        container_path,
                        read_only,
                    )
                )

        cmd.append(self._image)

        log_cmd = _format_container_command_for_log(_redact_container_command_for_log(cmd))
        logger.info(f"Starting container using {self._runtime}: {log_cmd}")

        # cmd was built as [runtime, "run", ...]; _cli re-adds the runtime.
        result = self._cli(
            *cmd[1:],
            timeout=300,
            intent=f"start sandbox container {container_name}",
        )
        if result.ok:
            container_id = result.stdout.strip()
            logger.info(f"Started container {container_name} (ID: {container_id}) using {self._runtime}")
            return container_id, preview_ports
        # Release the preview host ports we reserved for this attempt so the
        # create() retry loop can reallocate cleanly without leaking ports.
        for host_preview_port in preview_ports.values():
            release_port(host_preview_port)
        stderr = result.stderr or result.error or ""
        logger.error(f"Failed to start container using {self._runtime}: {stderr}")
        raise RuntimeError(f"Failed to start sandbox container: {stderr}")

    def _stop_container(self, container_id: str) -> None:
        """Stop a container (--rm ensures automatic removal)."""
        result = self._cli(
            "stop",
            container_id,
            timeout=60,
            intent=f"stop sandbox container {container_id}",
        )
        if result.ok:
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        else:
            logger.warning(f"Failed to stop container {container_id}: {result.stderr or result.error}")

    def _is_container_running(self, container_name: str) -> bool:
        """Check if a named container is currently running.

        This enables cross-process container discovery — any process can detect
        containers started by another process via the deterministic container name.

        Raises:
            RuntimeError: If the container runtime cannot answer the inspect
                query. A failed check is intentionally distinct from a
                definitive "container does not exist" result so callers do not
                destroy healthy containers during transient Docker/Container
                daemon failures.
        """
        result = self._cli(
            "inspect",
            "-f",
            "{{.State.Running}}",
            container_name,
            timeout=5,
            intent=f"check container {container_name} running",
        )
        if result.status is ExecutionStatus.TIMED_OUT:
            raise RuntimeError(f"Timed out checking container {container_name}")

        if result.exit_code == 0:
            return result.stdout.strip().lower() == "true"
        if result.exit_code is not None and _is_no_such_container_error(result.stderr, container_name):
            return False
        raise RuntimeError(f"Failed to inspect container {container_name}: {(result.stderr or result.error or '').strip()}")

    def _get_container_port(self, container_name: str) -> int | None:
        """Get the host port of a running container.

        Args:
            container_name: The container name to inspect.

        Returns:
            The host port mapped to container port 8080, or None if not found.
        """
        result = self._cli(
            "port",
            container_name,
            "8080",
            timeout=5,
            intent=f"resolve host port for {container_name}",
        )
        if result.ok and result.stdout.strip():
            # Output format: "0.0.0.0:PORT" or ":::PORT"
            try:
                port_str = result.stdout.strip().split(":")[-1]
                return int(port_str)
            except ValueError:
                pass
        return None

    def _get_container_preview_ports(self, container_name: str) -> dict[int, int]:
        """Resolve the published host ports for each configured preview port.

        Used when adopting an existing container during discovery so the
        dev-server preview can be reattached after a gateway restart. Missing
        mappings are silently skipped.
        """
        preview_ports: dict[int, int] = {}
        for container_preview_port in self._preview_container_ports:
            result = self._cli(
                "port",
                container_name,
                str(container_preview_port),
                timeout=5,
                intent=f"resolve preview port {container_preview_port} for {container_name}",
            )
            if result.ok and result.stdout.strip():
                try:
                    port_str = result.stdout.strip().splitlines()[0].split(":")[-1]
                    preview_ports[container_preview_port] = int(port_str)
                except ValueError:
                    continue
        return preview_ports
