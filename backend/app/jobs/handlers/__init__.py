"""Handler registration. Each feature adds its handlers here."""

from __future__ import annotations

from deerflow.jobs.registry import JobRegistry

from .demo import register as register_demo


def build_registry() -> JobRegistry:
    registry = JobRegistry()
    register_demo(registry)
    return registry
