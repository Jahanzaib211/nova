"""The `task` tool's per-task timeout: optional, clamped, and honoured.

`config.yaml` used to give `general-purpose` a 45-minute bound (2700 s).
That is too long a bound to be the default, but this harness legitimately
runs long tasks — so the requirement is not "shorter" but "configurable per
task, with a saner default a long task can opt out of".

The rules this pins:

- the model may ask for a longer budget than the agent's default;
- the request is CLAMPED to `subagents.max_timeout_seconds`, never
  rejected — a run must never fail because of a number a model chose;
- a value that is not a positive number is ignored (the configured default
  is kept), never guessed at, and never raises inside the tool call;
- omitting the argument changes nothing at all;
- the resolved value is what the executor actually enforces.
"""

from __future__ import annotations

import asyncio
import importlib
from enum import Enum
from types import SimpleNamespace

import pytest

from deerflow.config.subagents_config import (
    SubagentsAppConfig,
    clamp_task_timeout,
    get_subagents_app_config,
    load_subagents_config_from_dict,
)
from deerflow.subagents.registry import get_subagent_config

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


class _FakeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_runtime(*, app_config=None) -> SimpleNamespace:
    context = {"thread_id": "thread-1"}
    if app_config is not None:
        context["app_config"] = app_config
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context=context,
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_result(status, *, result=None, error=None):
    return SimpleNamespace(
        status=status,
        ai_messages=[],
        result=result,
        error=error,
        token_usage_records=[],
        usage_reported=True,
    )


async def _no_sleep(_: float) -> None:
    return None


def _run_task_tool(**kwargs) -> str:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


def _wire_task_tool(monkeypatch, captured: dict, *, status=_FakeStatus.COMPLETED, result="done", error=None):
    """Patch everything the tool touches, capturing the executor's config."""

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            captured["prompt"] = prompt
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", _FakeStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: _make_result(status, result=result, error=error),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kwargs: [])


@pytest.fixture
def subagents_config():
    """Restore the global subagents config after each test."""
    yield
    load_subagents_config_from_dict({})


# ---------------------------------------------------------------------------
# the pure clamp
# ---------------------------------------------------------------------------


def test_clamp_keeps_a_value_inside_the_range():
    assert clamp_task_timeout(3600, maximum=5400) == 3600


def test_clamp_cuts_a_value_above_the_maximum():
    assert clamp_task_timeout(999999, maximum=5400) == 5400


def test_clamp_ignores_an_unusable_value():
    """A malformed number is ignored, never guessed at."""
    for bad in (None, 0, -5, -0.5, "abc", "", "  ", True, False, float("inf"), float("nan"), object()):
        assert clamp_task_timeout(bad, maximum=5400) is None


def test_clamp_accepts_a_numeric_string():
    assert clamp_task_timeout("3600", maximum=5400) == 3600


def test_clamp_never_returns_less_than_the_minimum():
    assert clamp_task_timeout(1, maximum=5400, minimum=1) == 1


def test_the_configured_maximum_defaults_to_thirty_minutes():
    assert SubagentsAppConfig().max_timeout_seconds == 1800


# ---------------------------------------------------------------------------
# the registry: get_subagent_config() resolves the request
# ---------------------------------------------------------------------------


def test_a_request_above_the_maximum_is_clamped(subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    config = get_subagent_config("general-purpose", timeout_seconds=999999)
    assert config is not None
    assert config.timeout_seconds == 5400


def test_a_request_inside_the_bounds_is_honoured(subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    config = get_subagent_config("general-purpose", timeout_seconds=3600)
    assert config is not None
    assert config.timeout_seconds == 3600


def test_omitting_the_argument_preserves_the_configured_default(subagents_config):
    load_subagents_config_from_dict(
        {
            "timeout_seconds": 1800,
            "max_timeout_seconds": 5400,
            "agents": {"general-purpose": {"timeout_seconds": 1800}},
        }
    )
    config = get_subagent_config("general-purpose")
    assert config is not None
    assert config.timeout_seconds == 1800


def test_a_negative_request_is_ignored_and_keeps_the_default(subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    config = get_subagent_config("general-purpose", timeout_seconds=-5)
    assert config is not None
    assert config.timeout_seconds == 1800


def test_a_non_numeric_request_is_ignored_and_keeps_the_default(subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    config = get_subagent_config("general-purpose", timeout_seconds="not a number")
    assert config is not None
    assert config.timeout_seconds == 1800


def test_the_clamp_never_shortens_an_operator_configured_timeout(subagents_config):
    """The ceiling is the greater of the configured maximum and the agent's
    own timeout: the clamp stops a model from inventing a number, it does
    not shorten the operator's decision."""
    load_subagents_config_from_dict(
        {
            "timeout_seconds": 1800,
            "max_timeout_seconds": 1800,
            "agents": {"general-purpose": {"timeout_seconds": 2700}},
        }
    )
    config = get_subagent_config("general-purpose", timeout_seconds=999999)
    assert config is not None
    assert config.timeout_seconds == 2700


def test_the_request_does_not_leak_into_the_builtin_definition(subagents_config):
    from deerflow.subagents.builtins import BUILTIN_SUBAGENTS

    original = BUILTIN_SUBAGENTS["general-purpose"].timeout_seconds
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    get_subagent_config("general-purpose", timeout_seconds=3600)
    assert BUILTIN_SUBAGENTS["general-purpose"].timeout_seconds == original


# ---------------------------------------------------------------------------
# the task tool
# ---------------------------------------------------------------------------


def test_the_tool_exposes_the_argument_to_the_model():
    """The schema is derived from the docstring, so the argument has to be
    in the signature *and* in the Args block."""
    args = task_tool_module.task_tool.args
    assert "timeout_seconds" in args
    assert "timeout_seconds" in (task_tool_module.task_tool.description or "")


def test_the_tool_forwards_the_requested_timeout_to_the_executor(monkeypatch, subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    captured: dict = {}
    _wire_task_tool(monkeypatch, captured, status=_FakeStatus.COMPLETED)

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="long sweep",
        prompt="research every source",
        subagent_type="general-purpose",
        tool_call_id="tc-timeout",
        timeout_seconds=3600,
    )

    assert output.startswith("Task Succeeded")
    resolved = captured["executor_kwargs"]["config"]
    assert isinstance(resolved.timeout_seconds, int)
    assert resolved.timeout_seconds == 3600


def test_the_tool_clamps_an_oversized_request_before_the_executor_sees_it(monkeypatch, subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    captured: dict = {}
    _wire_task_tool(monkeypatch, captured, status=_FakeStatus.COMPLETED)

    _run_task_tool(
        runtime=_make_runtime(),
        description="runaway request",
        prompt="research every source",
        subagent_type="general-purpose",
        tool_call_id="tc-clamped",
        timeout_seconds=999999,
    )

    assert captured["executor_kwargs"]["config"].timeout_seconds == 5400


def test_the_tool_keeps_the_configured_default_when_the_argument_is_omitted(monkeypatch, subagents_config):
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    captured: dict = {}
    _wire_task_tool(monkeypatch, captured, status=_FakeStatus.COMPLETED)

    _run_task_tool(
        runtime=_make_runtime(),
        description="ordinary task",
        prompt="do the thing",
        subagent_type="general-purpose",
        tool_call_id="tc-default",
    )

    assert captured["executor_kwargs"]["config"].timeout_seconds == 1800


def test_the_tool_survives_a_malformed_timeout(monkeypatch, subagents_config):
    """A number the model made up must never crash the tool call."""
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    captured: dict = {}
    _wire_task_tool(monkeypatch, captured, status=_FakeStatus.COMPLETED)

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="nonsense budget",
        prompt="do the thing",
        subagent_type="general-purpose",
        tool_call_id="tc-garbage",
        timeout_seconds=-1,
    )

    assert output.startswith("Task Succeeded")
    assert captured["executor_kwargs"]["config"].timeout_seconds == 1800


def test_the_polling_budget_follows_the_resolved_timeout(monkeypatch, subagents_config):
    """The tool's own safety net is derived from the resolved timeout, so a
    longer per-task budget is actually waited for."""
    load_subagents_config_from_dict({"timeout_seconds": 1800, "max_timeout_seconds": 5400})
    captured: dict = {}
    _wire_task_tool(monkeypatch, captured, status=_FakeStatus.COMPLETED)

    _run_task_tool(
        runtime=_make_runtime(),
        description="long sweep",
        prompt="research every source",
        subagent_type="general-purpose",
        tool_call_id="tc-poll",
        timeout_seconds=5400,
    )

    # (timeout + 60) // 5 polls at five seconds each.
    assert captured["executor_kwargs"]["config"].timeout_seconds == 5400


def test_the_global_config_object_is_restored_between_tests():
    """The registry reads the global config object; make sure the fixture's
    teardown really puts it back (a leaking 5400 s max would silently widen
    every later test)."""
    assert get_subagents_app_config().max_timeout_seconds == 1800
