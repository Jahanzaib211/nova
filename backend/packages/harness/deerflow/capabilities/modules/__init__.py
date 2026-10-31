"""Harness-side capability modules. Each file declares one
:class:`CapabilityModule` (``_common`` holds shared helpers, not a module);
``register_builtin_modules`` wires them into the process registry. The one
gateway-side module — ``sessions`` (browser sessions and harness tokens) —
is registered by ``app.gateway.capabilities_modules`` because it needs
``app`` objects the harness may not import.
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
        secure_ship,
        skills,
        updates,
    )

    for mod in (jobs, integrations, agents, acp, models, skills, mcp, secrets, features, updates, runtimes, sandbox, secure_ship):
        registry.register(mod.MODULE)
