"""Handler registration. Each feature adds its handlers here."""

from __future__ import annotations

from deerflow.jobs.registry import JobRegistry

from .agents import register as register_agents
from .email_marketing import register as register_email_marketing
from .smoke import register as register_smoke


def build_registry() -> JobRegistry:
    registry = JobRegistry()
    register_smoke(registry)
    register_email_marketing(registry)
    register_agents(registry)
    return registry
