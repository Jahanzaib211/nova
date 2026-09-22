"""Harness-side capability modules. Each file declares one
:class:`CapabilityModule`; ``register_builtin_modules`` wires them into the
process registry. Gateway-side modules (auth sessions, channels, infra) are
registered by ``app.gateway.capabilities_modules`` because they need ``app``
objects the harness may not import.
"""

from __future__ import annotations

from deerflow.capabilities.registry import CapabilityRegistry


def register_builtin_modules(registry: CapabilityRegistry) -> None:
    from deerflow.capabilities.modules import (
        acp,
        agents,
        features,
        integrations,
        jobs,
        mcp,
        models,
        runtimes,
        sandbox,
        secrets,
        skills,
        updates,
    )

    for mod in (jobs, integrations, agents, acp, models, skills, mcp, secrets, features, updates, runtimes, sandbox):
        registry.register(mod.MODULE)
