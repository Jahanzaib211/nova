from pydantic import BaseModel, ConfigDict, Field


class VolumeMountConfig(BaseModel):
    """Configuration for a volume mount."""

    host_path: str = Field(
        ...,
        description=(
            "Source path for the mount. Resolution depends on the active provider: "
            "``LocalSandboxProvider`` checks this path from the gateway process — in "
            "``make dev`` that is the host machine, but in Docker deployments "
            "(``make up`` / docker-compose) it is the path *inside* the "
            "``deer-flow-gateway`` container, so the host directory must also be "
            "bind-mounted into the gateway service for the mount to take effect. "
            "``AioSandboxProvider`` (DooD) passes this value straight to ``docker -v`` "
            "for the sandbox container, where it is resolved by the host Docker daemon "
            "from the host machine's perspective."
        ),
    )
    container_path: str = Field(..., description="Path inside the container")
    read_only: bool = Field(default=False, description="Whether the mount is read-only")


class SandboxConfig(BaseModel):
    """Config section for a sandbox.

    Common options:
        use: Class path of the sandbox provider (required)
        allow_host_bash: Enable host-side bash execution for LocalSandboxProvider.
            Dangerous and intended only for fully trusted local workflows.

    AioSandboxProvider specific options:
        image: Docker image to use (default: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest)
        port: Base port for sandbox containers (default: 8080)
        replicas: Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.
        container_prefix: Prefix for container names (default: deer-flow-sandbox)
        idle_timeout: Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.
        privileged: Run containers with --privileged so the agent gets a nested Docker
            daemon. Effectively grants host root; off by default.
        memory_limit: Per-container --memory cap (default: 8g). None for unlimited.
        pids_limit: Per-container --pids-limit (default: 2048). None for unlimited.
        shm_size: Size of /dev/shm (default: 1g). Docker's 64 MB default hangs Chromium.
        mounts: List of volume mounts to share directories with the container
        environment: Environment variables to inject into the container (values starting with $ are resolved from host env)
    """

    use: str = Field(
        ...,
        description="Class path of the sandbox provider (e.g. deerflow.sandbox.local:LocalSandboxProvider)",
    )
    allow_host_bash: bool = Field(
        default=False,
        description="Allow the bash tool to execute directly on the host when using LocalSandboxProvider. Dangerous; intended only for fully trusted local environments.",
    )
    image: str | None = Field(
        default=None,
        description="Docker image to use for the sandbox container",
    )
    port: int | None = Field(
        default=None,
        description="Base port for sandbox containers",
    )
    replicas: int | None = Field(
        default=None,
        description="Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.",
    )
    container_prefix: str | None = Field(
        default=None,
        description="Prefix for container names",
    )
    idle_timeout: int | None = Field(
        default=None,
        description="Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.",
    )
    privileged: bool = Field(
        default=False,
        description=(
            "Run sandbox containers with --privileged, enabling the nested Docker daemon "
            "in the nova-sandbox-dind image layer. A privileged container is effectively "
            "host root: anything that escapes the sandbox reaches every other service on "
            "the host. Off by default; enable only when the agent genuinely needs to build "
            "or run containers, and only on a host you are willing to expose."
        ),
    )
    memory_limit: str | None = Field(
        default="8g",
        description=(
            "Per-container memory cap passed to --memory (e.g. '8g'). Set to null for no "
            "limit. Sandboxes ran unlimited until 2026-08-21, which on a box already "
            "committing more memory than it has meant one runaway build could take the "
            "whole machine down rather than just its own container."
        ),
    )
    pids_limit: int | None = Field(
        default=2048,
        description=("Per-container process cap passed to --pids-limit. Set to null for no limit. Bounds fork bombs and runaway build parallelism, which matters more once the sandbox can start containers of its own."),
    )
    shm_size: str | None = Field(
        default="1g",
        description=(
            "Size of /dev/shm in sandbox containers, passed to --shm-size. Docker's "
            "default is 64 MB, which is too small for Chromium: it loads a page fine "
            "and then hangs forever on screenshot or renderer work. The sandbox image "
            "ships a browser stack and Playwright, so this affects the agent's own "
            "browsing, not just test scripts. Set to null to use the Docker default."
        ),
    )
    mounts: list[VolumeMountConfig] = Field(
        default_factory=list,
        description="List of volume mounts to share directories between host and container",
    )
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to inject into the sandbox container. Values starting with $ will be resolved from host environment variables.",
    )

    bash_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from bash tool output. Output exceeding this limit is middle-truncated (head + tail), preserving the first and last half. Set to 0 to disable truncation.",
    )
    read_file_output_max_chars: int = Field(
        default=50000,
        ge=0,
        description="Maximum characters to keep from read_file tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )
    ls_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from ls tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )
    auto_detect_external_dev_server: bool = Field(
        default=False,
        description=(
            "When True, the Agent's Computer Browser tab scans a small set of well-known "
            "ports (3000, 5173, 8080, …) on each /dev-status poll and auto-registers any "
            "server it finds, without requiring an explicit call to "
            "``register_external_dev_server``. Useful when an agent starts a dev server "
            "via a raw ``bash`` tool instead of ``start_dev_server``. Default False to "
            "avoid surprising the user with a phantom preview when nothing was actually "
            "launched."
        ),
    )

    model_config = ConfigDict(extra="allow")
