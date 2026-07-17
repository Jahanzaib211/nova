"""Regression test: subagent _create_agent() must isolate from parent run checkpointer.

When a parent run carries a synchronous checkpointer (e.g. SqliteSaver via
DeerFlowClient), the subagent's ``agent.astream()`` inherits it through
``copy_context()`` + ``ensure_config()``. Without ``checkpointer=False``
at compile time, LangGraph's resolution prioritizes the inherited value
and calls the sync checkpointer's async methods, raising NotImplementedError.

The subagent is a one-shot delegation — it rebuilds state, calls astream
once, and extracts the last AIMessage. It never resumes, so persistence
is unnecessary and inheriting the parent checkpointer is harmful.

The executor's historical circular import is fixed in production code (pinned
by tests/test_import_hygiene.py), so this file imports the real modules — no
sys.modules mocking. The old machinery here left a stale executor module object
dangling as the ``deerflow.subagents.executor`` package attribute, which made
later executor tests patch the wrong module (order-dependent suite failures).
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _default_app_config():
    return SimpleNamespace(tool_search=SimpleNamespace(enabled=False))


@pytest.fixture(autouse=True)
def _setup_executor_module(monkeypatch):
    """Yield the real executor classes with a hermetic get_app_config seam."""
    import deerflow.subagents.executor as executor_module
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor

    monkeypatch.setattr(executor_module, "get_app_config", _default_app_config)

    yield {
        "SubagentConfig": SubagentConfig,
        "SubagentExecutor": SubagentExecutor,
        "executor_module": executor_module,
    }


class TestSubagentCheckpointerIsolation:
    """Verify _create_agent() unconditionally passes checkpointer=False to create_agent()."""

    def test_create_agent_receives_checkpointer_false(
        self,
        _setup_executor_module,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Assert checkpointer=False is always passed to create_agent()."""
        SubagentConfig = _setup_executor_module["SubagentConfig"]
        SubagentExecutor = _setup_executor_module["SubagentExecutor"]
        executor_module = _setup_executor_module["executor_module"]

        captured_kwargs: dict = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            agent = MagicMock()
            agent.checkpointer = False
            return agent

        def fake_build_subagent_runtime_middlewares(**kwargs):
            return []

        monkeypatch.setattr(executor_module, "create_agent", fake_create_agent)
        mw_module = ModuleType("deerflow.agents.middlewares.tool_error_handling_middleware")
        mw_module.build_subagent_runtime_middlewares = fake_build_subagent_runtime_middlewares
        monkeypatch.setitem(
            sys.modules,
            "deerflow.agents.middlewares.tool_error_handling_middleware",
            mw_module,
        )

        executor = SubagentExecutor(
            config=SubagentConfig(
                name="test",
                description="test",
                system_prompt="You are a test agent.",
            ),
            tools=[],
        )

        # Simulate lazy model_name resolution
        def fake_create_chat_model(**kwargs):
            return MagicMock()

        executor.model_name = "test-model"
        executor._base_tools = []

        monkeypatch.setattr(executor_module, "create_chat_model", fake_create_chat_model)
        monkeypatch.setattr(executor_module, "resolve_subagent_model_name", lambda config, parent, app_config=None: "test-model")

        result = executor._create_agent()

        assert captured_kwargs.get("checkpointer") is False, f"Expected checkpointer=False in create_agent() kwargs, got: {captured_kwargs.get('checkpointer')!r}"
        assert result.checkpointer is False
