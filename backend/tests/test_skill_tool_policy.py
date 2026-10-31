"""The skill allowed-tools policy, and the guard that would have caught #the-5-day-bug.

On 2026-08-25 15:56, `skills/public/security/SKILL.md` was added carrying an
`allowed-tools:` frontmatter block listing six tools. `allowed_tool_names_for_skills`
treats *any* declaration as switching the whole policy from allow-all to
allow-listed, and the default agent fed it *every enabled skill* -- so one public
skill, enabled by default and loaded on every run, collapsed the lead agent's
binding from 42 tools to 6. `task` was among the 36 dropped, so subagents were
dead for five days.

Nothing caught it. There was no test for this module at all, and the capabilities
panel reported the unfiltered registry, so the one instrument an operator checks
kept saying "42 tools".
"""

from dataclasses import dataclass

import pytest

from deerflow.skills.tool_policy import (
    CORE_TOOL_NAMES,
    allowed_tool_names_for_skills,
    filter_tools_by_skill_allowed_tools,
)


@dataclass
class FakeTool:
    name: str


@dataclass
class FakeSkill:
    name: str
    allowed_tools: list[str] | None = None


REGISTRY = [FakeTool(n) for n in ("bash", "read_file", "write_file", "str_replace", "grep_files", "browser_navigate", "task", "web_search", "view_image")]

# The exact block from skills/public/security/SKILL.md.
SECURITY_ALLOWED = ["bash", "read_file", "write_file", "str_replace", "grep_files", "browser_navigate"]


class TestAllowedToolNames:
    def test_no_skills_is_allow_all(self):
        assert allowed_tool_names_for_skills([]) is None

    def test_no_declaration_is_allow_all(self):
        skills = [FakeSkill("security"), FakeSkill("pine-script")]
        assert allowed_tool_names_for_skills(skills) is None

    def test_one_declaration_switches_the_whole_policy(self):
        """The sharp edge, pinned deliberately.

        This is not a bug in isolation -- it is the documented union rule. It
        becomes a bug only when a skill nobody selected gets a vote, which is
        what `skills_for_tool_policy` now prevents.
        """
        skills = [FakeSkill("security", SECURITY_ALLOWED), FakeSkill("pine-script")]
        assert allowed_tool_names_for_skills(skills) == set(SECURITY_ALLOWED)

    def test_declarations_union(self):
        skills = [FakeSkill("a", ["bash"]), FakeSkill("b", ["read_file"])]
        assert allowed_tool_names_for_skills(skills) == {"bash", "read_file"}


class TestFilter:
    def test_allow_all_returns_every_tool(self):
        assert filter_tools_by_skill_allowed_tools(REGISTRY, []) == REGISTRY

    def test_restricts_to_the_declared_set(self):
        kept = {t.name for t in filter_tools_by_skill_allowed_tools(REGISTRY, [FakeSkill("security", SECURITY_ALLOWED)])}
        assert kept == set(SECURITY_ALLOWED) | CORE_TOOL_NAMES

    @pytest.mark.parametrize("core", sorted(CORE_TOOL_NAMES))
    def test_core_tools_survive_any_policy(self, core):
        """No skill may amputate a core tool. `task` is the one that mattered."""
        kept = {t.name for t in filter_tools_by_skill_allowed_tools(REGISTRY, [FakeSkill("narrow", ["read_file"])])}
        assert core in kept

    def test_empty_declaration_still_keeps_core(self):
        kept = {t.name for t in filter_tools_by_skill_allowed_tools(REGISTRY, [FakeSkill("empty", [])])}
        assert kept == set(CORE_TOOL_NAMES)

    def test_rescue_is_logged_with_the_declaring_skill(self, caplog):
        """Silence is what let this run for five days -- name the culprit."""
        with caplog.at_level("WARNING"):
            filter_tools_by_skill_allowed_tools(REGISTRY, [FakeSkill("security", SECURITY_ALLOWED)])
        assert "task" in caplog.text
        assert "security" in caplog.text
