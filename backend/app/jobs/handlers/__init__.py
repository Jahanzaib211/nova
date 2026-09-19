"""Handler registration. Each feature adds its handlers here."""

from __future__ import annotations

from deerflow.jobs.registry import JobRegistry

from .demo import register as register_demo
from .email_marketing import register as register_email_marketing


def build_registry() -> JobRegistry:
    registry = JobRegistry()
    register_demo(registry)
    register_email_marketing(registry)
    return registry
