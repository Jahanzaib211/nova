from typing import TYPE_CHECKING

from .features import Next, Prev, RuntimeFeatures
from .thread_state import SandboxState, ThreadState

if TYPE_CHECKING:
    from .factory import create_deerflow_agent
    from .lead_agent import make_lead_agent
    from .lead_agent.prompt import prime_enabled_skills_cache

# The lead-agent stack (factory, lead_agent, its middlewares) is imported
# lazily via PEP 562. Eager imports here made every `deerflow.agents.*`
# submodule import (e.g. `agents.thread_state` from subagents/executor.py)
# pull the full lead-agent graph, which re-enters deerflow.subagents while it
# is still initializing (see tests/test_import_hygiene.py). Skills-cache
# priming moved to lead_agent/__init__, so it still runs when LangGraph
# resolves make_lead_agent for graph registration.
_LAZY_EXPORTS = {
    "create_deerflow_agent": ("deerflow.agents.factory", "create_deerflow_agent"),
    "make_lead_agent": ("deerflow.agents.lead_agent", "make_lead_agent"),
    "prime_enabled_skills_cache": ("deerflow.agents.lead_agent.prompt", "prime_enabled_skills_cache"),
}

__all__ = [
    "create_deerflow_agent",
    "RuntimeFeatures",
    "Next",
    "Prev",
    "make_lead_agent",
    "SandboxState",
    "ThreadState",
]


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
