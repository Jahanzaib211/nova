"""Command models — executable commands inferred from project metadata.

Phase C9 — Nova never guesses commands.  Every executable command
is inferred from project configuration and indexed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandKind(str, Enum):
    """Kind of command."""

    DEV_SERVER = "dev_server"
    BUILD = "build"
    PREVIEW = "preview"
    TEST = "test"
    LINT = "lint"
    TYPE_CHECK = "type_check"
    FORMAT = "format"
    MIGRATE = "migrate"
    SEED = "seed"
    WORKER = "worker"
    SERVE = "serve"
    DOCKER_BUILD = "docker_build"
    DOCKER_RUN = "docker_run"
    COMPOSE_UP = "compose_up"
    COMPOSE_DOWN = "compose_down"
    PM2_START = "pm2_start"
    PM2_RESTART = "pm2_restart"
    PM2_STOP = "pm2_stop"
    SYSTEMD_START = "systemd_start"
    SYSTEMD_RESTART = "systemd_restart"
    SYSTEMD_STOP = "systemd_stop"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Command:
    """An executable command inferred from project configuration."""

    command_id: str
    name: str
    kind: CommandKind
    project_id: str
    argv: tuple[str, ...]
    cwd: str = ""
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    required_tools: tuple[str, ...] = field(default_factory=tuple)
    is_background: bool = False
    is_destructive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", CommandKind(self.kind))
