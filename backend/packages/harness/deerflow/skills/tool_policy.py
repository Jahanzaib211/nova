import logging
from typing import Protocol

from deerflow.skills.types import Skill

logger = logging.getLogger(__name__)


class NamedTool(Protocol):
    name: str


# Tools a skill's allowed-tools may never remove.
#
# A skill declares which tools *it* needs; it is not a mechanism for taking the
# agent's core capabilities away. `task` is the load-bearing entry: it is how
# subagents are dispatched, and a skill silently dropping it presents to the
# user as "subagents are broken", with an error naming the surviving tools and
# nothing pointing at the skill that caused it. That cost five days.
#
# Deliberately small. This is a floor against accidental amputation, not a
# second allowlist -- an operator who wants a genuinely narrow agent should say
# so in that agent's config rather than have every skill silently widened.
CORE_TOOL_NAMES = frozenset({"task"})


def allowed_tool_names_for_skills(skills: list[Skill]) -> set[str] | None:
    """Return the union of explicit skill allowed-tools declarations.

    None means legacy allow-all behavior. It is returned only when no loaded
    skill declares allowed-tools. Once any skill declares the field, legacy
    skills without the field contribute no tools instead of disabling the
    explicit restrictions from other skills.
    """
    if not skills:
        return None

    allowed: set[str] = set()
    has_explicit_declaration = False
    for skill in skills:
        if skill.allowed_tools is None:
            continue
        has_explicit_declaration = True
        if not skill.allowed_tools:
            logger.info("Skill %s declared empty allowed-tools", skill.name)
        allowed.update(skill.allowed_tools)

    if not has_explicit_declaration:
        return None
    return allowed


def filter_tools_by_skill_allowed_tools[ToolT: NamedTool](tools: list[ToolT], skills: list[Skill]) -> list[ToolT]:
    """Restrict *tools* to what the selected skills allow, never below the core set.

    Callers decide *which* skills get a say (see
    ``_load_enabled_skills_for_tool_policy``); this decides what a say means.
    """
    allowed = allowed_tool_names_for_skills(skills)
    if allowed is None:
        return tools

    kept = [tool for tool in tools if tool.name in allowed or tool.name in CORE_TOOL_NAMES]

    # Announce the near-miss. Silence here is what turned a one-line frontmatter
    # change into a five-day outage: the tool vanished, the capabilities panel
    # kept reporting the unfiltered registry, and nothing anywhere named the
    # skill responsible.
    rescued = sorted({tool.name for tool in kept if tool.name in CORE_TOOL_NAMES and tool.name not in allowed})
    if rescued:
        declaring = sorted(skill.name for skill in skills if skill.allowed_tools is not None)
        logger.warning(
            "Skill allowed-tools policy would have dropped core tool(s) %s; keeping them. Declaring skill(s): %s",
            ", ".join(rescued),
            ", ".join(declaring) or "<none>",
        )
    return kept
