"""The todo list must exist outside Plan Mode.

`_create_todo_list_middleware` used to return ``None`` unless ``is_plan_mode``
was set, so ``write_todos`` did not exist in a normal run. A long autonomous
task — exactly where a user most wants to watch progress — had no tracking at
all, and the UI's todo panel never appeared. That reads as "the panel is
broken" rather than "the tool was never bound", which is why it went unfixed.

Plan mode still means something: it changes how hard the prompt pushes the
agent to write the list up front. It no longer decides whether the tool exists.
"""

from __future__ import annotations

import pytest

from deerflow.agents.lead_agent import agent as lead_agent_module


@pytest.mark.parametrize("is_plan_mode", [True, False])
def test_todo_middleware_is_always_created(is_plan_mode: bool) -> None:
    middleware = lead_agent_module._create_todo_list_middleware(is_plan_mode)

    assert middleware is not None, "write_todos must exist regardless of plan mode"
    assert type(middleware).__name__ == "TodoMiddleware"


def test_plan_mode_only_changes_the_prompt() -> None:
    """The distinction survives, but as emphasis rather than availability."""
    planned = lead_agent_module._create_todo_list_middleware(True)
    normal = lead_agent_module._create_todo_list_middleware(False)

    planned_prompt = getattr(planned, "system_prompt", "") or ""
    normal_prompt = getattr(normal, "system_prompt", "") or ""

    assert "asked for a plan" in planned_prompt
    assert "asked for a plan" not in normal_prompt


def test_prompts_still_carry_the_rules_that_matter() -> None:
    """Condensing the guidance must not drop the rules it existed to enforce.

    The old text stated the same "3+ steps / not for trivial work" rule three
    times across two strings. The repetition is gone; the rules are not.
    """
    middleware = lead_agent_module._create_todo_list_middleware(False)
    combined = ((getattr(middleware, "system_prompt", "") or "") + (getattr(middleware, "tool_description", "") or "")).lower()

    for rule in ("3+ distinct steps", "in_progress", "completed", "never batch"):
        assert rule.lower() in combined, f"lost guidance: {rule}"
