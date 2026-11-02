"""Runtime path resolution for standalone harness usage."""

import os
from pathlib import Path


def project_root() -> Path:
    """Return the caller project root for runtime-owned files."""
    if env_root := os.getenv("DEER_FLOW_PROJECT_ROOT"):
        root = Path(env_root).resolve()
        if not root.exists():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' does not exist.")
        if not root.is_dir():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' is not a directory.")
        return root
    return Path.cwd().resolve()


def set_project_root_from_cwd(*, overwrite: bool = False) -> Path | None:
    """Promote ``Path.cwd()`` to ``DEER_FLOW_PROJECT_ROOT`` for this process.

    Useful for ad-hoc scripts and test harnesses that want the same
    resolution semantics as the running gateway. Idempotent: if the
    env var is already set and ``overwrite`` is False, returns the
    existing resolved root without touching the environment.

    Args:
        overwrite: If True, replace an existing env-var value with the
            current cwd. Default False (safer — explicit opt-in).

    Returns:
        The resolved project root (the newly-set value, or the existing
        one if the call was a no-op). Returns None only when cwd does
        not exist (rare, e.g. the process's CWD was deleted underneath it).
    """
    existing = os.getenv("DEER_FLOW_PROJECT_ROOT")
    if existing and not overwrite:
        return Path(existing).resolve()
    cwd = Path.cwd().resolve()
    if not cwd.exists() or not cwd.is_dir():
        return None
    os.environ["DEER_FLOW_PROJECT_ROOT"] = str(cwd)
    return cwd


def in_container() -> bool:
    """Best-effort detection of whether we are running inside a container.

    Used by the agent manifest to decide whether to surface container-
    specific tooling notes (e.g. ``/app`` project root is canonical
    inside the gateway container). Detection is heuristic and may yield
    false negatives on exotic setups; callers should treat the result as
    advisory, not authoritative.
    """
    # /proc/1/cgroup exists on Linux containers and bare-metal alike;
    # the "docker" / "kubepods" / "containerd" markers are container-only.
    try:
        cgroup_path = Path("/proc/1/cgroup")
        if cgroup_path.is_file():
            text = cgroup_path.read_text(errors="ignore")
            if any(marker in text for marker in ("docker", "kubepods", "containerd", "lxc")):
                return True
    except OSError:
        pass
    # /.dockerenv is a Docker-specific sentinel created in every container.
    if Path("/.dockerenv").exists():
        return True
    return False


def runtime_home() -> Path:
    """Return the writable DeerFlow state directory."""
    if env_home := os.getenv("DEER_FLOW_HOME"):
        return Path(env_home).resolve()
    return project_root() / ".deer-flow"


def resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    """Resolve absolute paths as-is and relative paths against the project root."""
    path = Path(value)
    if not path.is_absolute():
        path = (base or project_root()) / path
    return path.resolve()


def existing_project_file(names: tuple[str, ...]) -> Path | None:
    """Return the first existing named file under the project root."""
    root = project_root()
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
