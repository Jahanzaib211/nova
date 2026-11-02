"""Subagent registry for managing available subagents."""

import logging
from dataclasses import replace
from typing import Any

from nova.sandbox.security import is_host_bash_allowed
from nova.subagents.builtins import BUILTIN_SUBAGENTS
from nova.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def _resolve_subagents_app_config(app_config: Any | None = None):
    if app_config is None:
        from nova.config.subagents_config import get_subagents_app_config

        return get_subagents_app_config()
    return getattr(app_config, "subagents", app_config)


def _build_custom_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """Build a SubagentConfig from config.yaml custom_agents section.

    Args:
        name: The name of the custom subagent.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve from.

    Returns:
        SubagentConfig if found in custom_agents, None otherwise.
    """
    subagents_config = _resolve_subagents_app_config(app_config)
    custom = subagents_config.custom_agents.get(name)
    if custom is None:
        return None

    return SubagentConfig(
        name=name,
        description=custom.description,
        system_prompt=custom.system_prompt,
        tools=custom.tools,
        disallowed_tools=custom.disallowed_tools,
        skills=custom.skills,
        model=custom.model,
        max_turns=custom.max_turns,
        timeout_seconds=custom.timeout_seconds,
    )


def get_subagent_config(name: str, *, app_config: Any | None = None, timeout_seconds: int | None = None) -> SubagentConfig | None:
    """Get a subagent configuration by name, with config.yaml overrides applied.

    Resolution order (mirrors Codex's config layering):
    1. Built-in subagents (general-purpose, bash)
    2. Custom subagents from config.yaml custom_agents section
    3. Per-agent overrides from config.yaml agents section (timeout, max_turns, model, skills)
    4. An optional per-task timeout requested by the caller

    Args:
        name: The name of the subagent.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve overrides from.
        timeout_seconds: Optional per-task timeout — the ``task`` tool's
            ``timeout_seconds`` argument. It is CLAMPED, never rejected:
            the ceiling is ``subagents.max_timeout_seconds``, raised to
            the agent's own configured timeout so an operator's setting is
            never shortened. A value that is not a positive number is
            ignored and the configured timeout is kept.

    Returns:
        SubagentConfig if found (with any config.yaml overrides applied), None otherwise.
    """
    # Step 1: Look up built-in, then fall back to custom_agents
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)
    if config is None:
        return None

    # Step 2: Apply per-agent overrides from config.yaml agents section.
    # Only explicit per-agent overrides are applied here. Global defaults
    # (timeout_seconds, max_turns at the top level) apply to built-in agents
    # but must NOT override custom agents' own values — custom agents define
    # their own defaults in the custom_agents section.
    subagents_config = _resolve_subagents_app_config(app_config)
    is_builtin = name in BUILTIN_SUBAGENTS
    agent_override = subagents_config.agents.get(name)

    overrides = {}

    # Timeout: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.timeout_seconds is not None:
        if agent_override.timeout_seconds != config.timeout_seconds:
            logger.debug("Subagent '%s': timeout overridden (%ss -> %ss)", name, config.timeout_seconds, agent_override.timeout_seconds)
            overrides["timeout_seconds"] = agent_override.timeout_seconds
    elif is_builtin and subagents_config.timeout_seconds != config.timeout_seconds:
        logger.debug("Subagent '%s': timeout from global default (%ss -> %ss)", name, config.timeout_seconds, subagents_config.timeout_seconds)
        overrides["timeout_seconds"] = subagents_config.timeout_seconds

    # Max turns: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.max_turns is not None:
        if agent_override.max_turns != config.max_turns:
            logger.debug("Subagent '%s': max_turns overridden (%s -> %s)", name, config.max_turns, agent_override.max_turns)
            overrides["max_turns"] = agent_override.max_turns
    elif is_builtin and subagents_config.max_turns is not None and subagents_config.max_turns != config.max_turns:
        logger.debug("Subagent '%s': max_turns from global default (%s -> %s)", name, config.max_turns, subagents_config.max_turns)
        overrides["max_turns"] = subagents_config.max_turns

    # Model: per-agent override only (no global default for model)
    effective_model = subagents_config.get_model_for(name)
    if effective_model is not None and effective_model != config.model:
        logger.debug("Subagent '%s': model overridden (%s -> %s)", name, config.model, effective_model)
        overrides["model"] = effective_model

    # Skills: per-agent override only (no global default for skills)
    effective_skills = subagents_config.get_skills_for(name)
    if effective_skills is not None and effective_skills != config.skills:
        logger.debug("Subagent '%s': skills overridden (%s -> %s)", name, config.skills, effective_skills)
        overrides["skills"] = effective_skills

    # Per-task timeout.
    #
    # The `task` tool lets the model ask for a longer bound than the
    # agent's default — a deep research sweep legitimately needs more than
    # 30 minutes — but it must not be able to ask for an unbounded one.
    # So the value is clamped rather than rejected: a run must never fail
    # because of a number a model chose.
    #
    # The ceiling is the configured maximum, raised to the agent's own
    # effective timeout when the operator gave that agent more than the
    # maximum. The clamp exists to stop a model from inventing a number,
    # not to shorten the operator's decision.
    if timeout_seconds is not None:
        from nova.config.subagents_config import DEFAULT_MAX_TIMEOUT_SECONDS, clamp_task_timeout

        effective_timeout = overrides.get("timeout_seconds", config.timeout_seconds)
        max_timeout = getattr(subagents_config, "max_timeout_seconds", None) or DEFAULT_MAX_TIMEOUT_SECONDS
        ceiling = max(effective_timeout, int(max_timeout))
        clamped = clamp_task_timeout(timeout_seconds, maximum=ceiling)
        if clamped is None:
            logger.warning(
                "Subagent '%s': ignoring unusable timeout_seconds=%r — keeping the configured %ss",
                name,
                timeout_seconds,
                effective_timeout,
            )
        else:
            if clamped != effective_timeout:
                logger.debug("Subagent '%s': timeout from the task request (%ss -> %ss, ceiling %ss)", name, effective_timeout, clamped, ceiling)
            overrides["timeout_seconds"] = clamped

    if overrides:
        config = replace(config, **overrides)

    return config


def list_subagents(*, app_config: Any | None = None) -> list[SubagentConfig]:
    """List all available subagent configurations (with config.yaml overrides applied).

    Returns:
        List of all registered SubagentConfig instances (built-in + custom).
    """
    configs = []
    for name in get_subagent_names(app_config=app_config):
        config = get_subagent_config(name, app_config=app_config)
        if config is not None:
            configs.append(config)
    return configs


def get_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """Get all available subagent names (built-in + custom).

    Returns:
        List of subagent names.
    """
    names = list(BUILTIN_SUBAGENTS.keys())

    # Merge custom_agents from config.yaml
    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in subagents_config.custom_agents:
        if custom_name not in names:
            names.append(custom_name)

    return names


def get_available_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """Get subagent names that should be exposed to the active runtime.

    Returns:
        List of subagent names visible to the current sandbox configuration.
    """
    names = get_subagent_names(app_config=app_config)
    try:
        host_bash_allowed = is_host_bash_allowed(app_config) if hasattr(app_config, "sandbox") else is_host_bash_allowed()
    except Exception:
        logger.debug("Could not determine host bash availability; exposing all subagents")
        return names

    if not host_bash_allowed:
        names = [name for name in names if name != "bash"]
    return names
