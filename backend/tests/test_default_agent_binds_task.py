"""The default agent must bind `task`, whatever skills are installed.

This is the assertion whose absence cost five days. On 2026-08-25 a single
public skill's `allowed-tools:` frontmatter collapsed the lead agent's binding
from 42 tools to 6; `task` was dropped, subagents returned

    Error: task is not a valid tool, try one of [write_todos, read_file, ...]

and a commit 58 minutes later that correctly made `subagent_enabled`
unconditional appeared to do nothing, because the tool was added and then
filtered away.

Deliberately end-to-end over the *real* skills directory: the bug lived in the
interaction between a repo data file and the policy, so a test with fake skills
would have stayed green through the entire outage.
"""

from deerflow.agents.lead_agent.agent import skills_for_tool_policy
from deerflow.config.app_config import get_app_config
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools


class _T:
    def __init__(self, name):
        self.name = name


def test_default_agent_is_not_restricted_by_merely_installed_skills():
    """`available_skills is None` is the default agent: no skill gets a vote."""
    assert skills_for_tool_policy(None, app_config=get_app_config()) == []


def test_task_survives_the_policy_for_the_default_agent():
    """The end-to-end guard, against whatever is actually in skills/."""
    config = get_app_config()
    registry = [_T(n) for n in ("bash", "read_file", "task", "web_search", "view_image")]
    kept = {t.name for t in filter_tools_by_skill_allowed_tools(registry, skills_for_tool_policy(None, app_config=config))}
    assert "task" in kept, "the default agent lost `task` -- subagents are dead"
    assert kept == {t.name for t in registry}, "an installed-but-unselected skill narrowed the default agent"


def test_a_selected_skill_still_restricts(monkeypatch):
    """The feature itself must keep working -- this is a scope fix, not a removal.

    Uses a synthetic declaring skill rather than whatever happens to be on disk.
    An earlier version of this test asked the real skills directory and *skipped*
    when nothing declared `allowed-tools` -- which is precisely the failure mode
    this file exists to prevent: a guard that quietly opts out is a guard that
    reports green through the outage it was written for. The conftest-sanitized
    config does not resolve the repo skills tree, so that skip was the normal
    path, not the exception.
    """
    from deerflow.agents.lead_agent import agent as agent_mod

    class FakeSkill:
        def __init__(self, name, allowed_tools):
            self.name = name
            self.allowed_tools = allowed_tools

    monkeypatch.setattr(
        agent_mod,
        "get_enabled_skills_for_config",
        lambda _cfg: [FakeSkill("security", ["bash", "read_file"]), FakeSkill("other", None)],
        raising=False,
    )
    monkeypatch.setitem(
        __import__("sys").modules["deerflow.agents.lead_agent.prompt"].__dict__,
        "get_enabled_skills_for_config",
        lambda _cfg: [FakeSkill("security", ["bash", "read_file"]), FakeSkill("other", None)],
    )

    config = get_app_config()
    registry = [_T(n) for n in ("bash", "read_file", "task", "web_search")]

    # Selected -> restricts.
    selected = skills_for_tool_policy({"security"}, app_config=config)
    assert any(s.allowed_tools is not None for s in selected), "fixture did not take effect"
    kept = {t.name for t in filter_tools_by_skill_allowed_tools(registry, selected)}
    assert "web_search" not in kept, "a selected skill's allowed-tools no longer restricts"
    assert "task" in kept, "core tools must survive even a selected skill's policy"

    # Not selected -> the default agent is untouched, even though the same
    # declaring skill is installed and enabled. This is the regression itself.
    kept_default = {t.name for t in filter_tools_by_skill_allowed_tools(registry, skills_for_tool_policy(None, app_config=config))}
    assert kept_default == {t.name for t in registry}
