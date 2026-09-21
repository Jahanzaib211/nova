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
        nofile_limit: Per-container open-file cap (--ulimit nofile, default 65535).
        cpu_shares: Relative CPU weight (--cpu-shares, default 512). Never throttles.
        cpu_limit: Hard --cpus quota. Off by default; throttles, so prefer cpu_shares.
        max_lifetime: Hard ceiling in seconds on container age, regardless of activity.
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
    startup_timeout: int | None = Field(
        default=None,
        description=(
            "Seconds to wait for a newly created sandbox to answer on its port "
            "(default: 180). This was hardcoded to 60, which is enough for a warm "
            "host and not enough for a cold one: the image boots supervisord, "
            "code-server, Jupyter, a VNC server and a browser, so under memory or "
            "IO pressure it routinely needs longer. A container that misses the "
            "deadline is destroyed and the turn fails with 'failed to become "
            "ready', even though the image is healthy and would have answered."
        ),
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
    cpu_shares: int | None = Field(
        default=512,
        description=(
            "Relative CPU weight passed to --cpu-shares. This is the knob to reach for, "
            "not cpu_limit. Shares only matter when the CPU is actually contended: an "
            "idle machine lets a sandbox use every core, and under load the scheduler "
            "simply prefers whoever has more shares. Nothing is ever throttled. "
            "Default 512 is half Docker's 1024, so Nova's own gateway and frontend win "
            "contention while a sandbox still runs flat out whenever they are idle. "
            "This matters because subagents share their parent thread's sandbox — an "
            "entire fan-out lives in one container, so a quota there would throttle the "
            "whole task."
        ),
    )
    cpu_limit: str | None = Field(
        default=None,
        description=(
            "Hard per-container CPU quota passed to --cpus. Off by default and usually "
            "the wrong tool: --cpus is a ceiling the kernel enforces by descheduling the "
            "container's threads once the quota is spent within each period, even when "
            "every other core is idle. On long-running agent work that shows up as "
            "stalls, not as slowness. Prefer cpu_shares. Set this only when a hard "
            "ceiling genuinely matters (shared tenancy, thermal limits)."
        ),
    )
    max_lifetime: int | None = Field(
        default=None,
        description=(
            "Hard ceiling in seconds on how long a sandbox container may live, "
            "regardless of activity. None disables it. `idle_timeout` only reaps "
            "sandboxes that go quiet; a container that keeps producing output — a "
            "watch loop, a dev server, a test that never converges — stays alive "
            "forever under an idle rule alone. This is the deadline that does not "
            "care whether the work looks busy."
        ),
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
    nofile_limit: int | None = Field(
        default=65535,
        description=(
            "Per-container open-file limit, passed to --ulimit nofile. Docker "
            "inherits the daemon's soft limit, which is 1024 here -- low enough "
            "that Chromium and parallel Node builds exhaust it and fail with "
            "EMFILE, which surfaces as an unrelated-looking crash rather than a "
            "resource error. Set to null to inherit the daemon default."
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
    stream_terminal_output: bool = Field(
        default=False,
        description=(
            "Stream bash output into sandbox.log as it is produced, instead of "
            "one line at completion. Emits extra `delta`/`replace` frames that "
            "only a frontend built after this feature understands. Default OFF "
            "because the backend hot-reloads while the frontend serves a "
            "prebuilt bundle: turning it on before the matching frontend is "
            "deployed makes every intermediate frame render as a blank row and "
            "evict real events from the 200-entry display window. Turn on only "
            "once the frontend serving this deployment understands the "
            "`sandbox_delta` SSE event."
        ),
    )
    observation_max_chars: int = Field(
        default=20000,
        ge=0,
        description=(
            "Maximum characters of tool output kept in one sandbox.log observation "
            "line -- what the Agent's Computer Terminal renders. Was a hardcoded "
            "2000 with no truncation marker, so the panel showed silently less "
            "than the model received for the same command. Middle-truncated with "
            "an explicit marker, like bash_output_max_chars. Set to 0 to disable."
        ),
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
            "NOT IMPLEMENTED — setting this has no effect. Kept only so an "
            "existing config.yaml that sets it still validates. The described "
            "port scan does not exist: this field has no reader anywhere in the "
            "codebase, while every other field here traces to "
            "aio_sandbox_provider -> local_backend -> the docker run argv. "
            "Intended behaviour was: on each /dev-status poll, scan well-known "
            "ports (3000, 5173, 8080, …) and auto-register any dev server "
            "found, so an agent that started one via raw ``bash`` rather than "
            "``start_dev_server`` still gets a preview. Implement it or delete "
            "the field; do not leave it looking like a working switch."
        ),
    )

    model_config = ConfigDict(extra="allow")
