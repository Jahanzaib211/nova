from .agent import make_lead_agent
from .prompt import prime_enabled_skills_cache

# LangGraph resolves make_lead_agent from this package when registering the
# graph. Prime the enabled-skills cache at that point so the request path can
# usually read a warm cache without synchronous filesystem work. (Previously
# in deerflow/agents/__init__.py; moved with the lazy-export refactor so that
# importing agents.thread_state alone no longer triggers filesystem access.)
prime_enabled_skills_cache()

__all__ = ["make_lead_agent"]
