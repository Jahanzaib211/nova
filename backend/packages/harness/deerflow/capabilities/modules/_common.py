"""Helpers shared by capability modules."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from deerflow.capabilities.types import ModuleStatus


class Empty(BaseModel):
    """No input."""


class Ok(BaseModel):
    ok: bool = True
    detail: str | None = None


class Items(BaseModel):
    items: list[dict[str, Any]]
    total: int


def config_section(name: str) -> Any:
    from deerflow.config.app_config import get_app_config

    return getattr(get_app_config(), name, None)


def section_enabled(name: str) -> bool:
    block = config_section(name)
    return bool(getattr(block, "enabled", False)) if block is not None else False


def status_from_flag(name: str, *, healthy_detail: str = "configured", off_detail: str = "disabled in config.yaml") -> ModuleStatus:
    on = section_enabled(name)
    return ModuleStatus(configured=on, healthy=on, detail=healthy_detail if on else off_detail)


def jobs_repo():
    """The job repository, or ``None`` when no database is configured."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.job.sql import JobRepository

    sf = get_session_factory()
    return JobRepository(sf) if sf is not None else None
