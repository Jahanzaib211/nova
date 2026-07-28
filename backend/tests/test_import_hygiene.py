"""Regression: core harness modules must import cleanly, without mocks.

The production import cycle this pins (fixed by moving MAX_CONCURRENT_SUBAGENTS
to ``subagents.config`` and importing the registry directly in the lead-agent
prompt) was:

    deerflow.subagents.executor
      -> deerflow.agents.thread_state
        -> deerflow.agents.__init__
          -> lead_agent.agent -> subagent_limit_middleware -> executor  (cycle)
          -> lead_agent.prompt -> deerflow.subagents.__init__ -> executor  (cycle)

Historically the suite papered over this with a ``sys.modules`` mock of
``deerflow.subagents.executor`` in conftest.py. These tests import in a fresh
subprocess so no conftest state, mock, or previously-imported module can hide
a regression.
"""

import subprocess
import sys

import pytest

_CYCLE_SENSITIVE_MODULES = [
    "deerflow.subagents.executor",
    "deerflow.subagents",
    "deerflow.agents",
    "deerflow.agents.middlewares.subagent_limit_middleware",
    "deerflow.agents.lead_agent.prompt",
]


@pytest.mark.parametrize("module_name", _CYCLE_SENSITIVE_MODULES)
def test_module_imports_cleanly_in_fresh_interpreter(module_name):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"`import {module_name}` failed in a fresh interpreter:\n{result.stderr}"


def test_constant_stays_importable_from_both_homes():
    """MAX_CONCURRENT_SUBAGENTS lives in subagents.config; executor re-exports it."""
    code = "from deerflow.subagents.config import MAX_CONCURRENT_SUBAGENTS as a; from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS as b; assert a is b and a == 3"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
