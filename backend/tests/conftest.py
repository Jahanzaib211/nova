"""Test configuration for the backend test suite.

Sets up sys.path and pre-mocks modules that would cause circular import
issues when unit-testing lightweight config/registry code in isolation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Make 'app' and 'deerflow' importable from any working directory
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Neutralize deployment-specific environment: the repo-root .env pins the
# deployment paths to their in-container locations (/app/...), which do not
# exist on the host. load_dotenv() (called at deerflow.config.app_config
# import time) would inject them into every host-side test run, making
# gateway config loading raise FileNotFoundError and every TestClient
# request fail with 503 — and channel/paths code mkdir() under /app.
# Pre-set the host-correct values *before* anything calls load_dotenv() —
# python-dotenv never overrides variables that are already set, so these
# win over the .env values. The container values are untouched (.env is
# not modified) and an operator's explicit shell exports still win over
# setdefault.
_repo_root = Path(__file__).resolve().parents[2]
_backend_root = Path(__file__).resolve().parents[1]
_host_env_defaults = {
    "DEER_FLOW_CONFIG_PATH": _repo_root / "config.yaml",
    "DEER_FLOW_EXTENSIONS_CONFIG_PATH": _repo_root / "extensions_config.json",
    "DEER_FLOW_REPO_ROOT": _repo_root,
    "DEER_FLOW_HOME": _backend_root / ".deer-flow",
}
for _var, _path in _host_env_defaults.items():
    if _path.exists():
        os.environ.setdefault(_var, str(_path))

# The .env also stamps DEER_FLOW_ENV=production (this box runs the live
# gateway). Tests must not inherit the deployment's environment identity:
# the auth-disabled safety veto would 401 every DEER_FLOW_AUTH_DISABLED
# test. Empty string reads as "unset" for every consumer (auth veto,
# telemetry env tags) and blocks load_dotenv from injecting the value;
# tests that exercise specific environments set it explicitly.
os.environ.setdefault("DEER_FLOW_ENV", "")


@pytest.fixture(autouse=True)
def _guard_process_managers_from_tests():
    """Interlock: no test may reach real ``pm2``/``systemctl`` via the DI kernel.

    Incident 2026-07-13: recovery-engine tests executed real
    ``sudo systemctl restart cloudflared-nova.service`` and
    ``pm2 restart deerflow`` on the production box (sudoers permits the
    restart), tripping systemd's start-limit and taking the live tunnel +
    gateway down mid-suite. The Execution Kernel (Phase C7) makes the
    guard structural: every test gets a container kernel whose policy
    denies the PM2 and SYSTEMD execution classes. Shell/git/docker/python
    stay available (sandbox E2E tests use them intentionally); tests that
    exercise pm2/systemd behavior must override ``execution_kernel`` with
    a FakeExecutionKernel.
    """
    from deerflow.execution import ExecutionKernel, PolicyEngine
    from deerflow.execution.models import ExecutionClass
    from deerflow.execution.policy import _DEFAULT_POLICIES, ClassPolicy
    from deerflow.services.container import service_container

    policies = dict(_DEFAULT_POLICIES)
    policies[ExecutionClass.PM2] = ClassPolicy(allowed_programs=("/pm2-denied-in-tests",))
    policies[ExecutionClass.SYSTEMD] = ClassPolicy(allowed_programs=("/systemctl-denied-in-tests",), allow_sudo=False)
    guarded = ExecutionKernel(policy_engine=PolicyEngine(policies=policies))
    service_container.override(execution_kernel=guarded)
    yield
    # Only clean up our own override — tests may have replaced it (their
    # fixtures own that lifecycle) or already reset the container.
    if service_container._overrides.get("execution_kernel") is guarded:
        service_container._overrides.pop("execution_kernel", None)
        service_container._singletons.pop("execution_kernel", None)

# Break the circular import chain that exists in production code:
#   deerflow.subagents.__init__
#     -> .executor (SubagentExecutor, SubagentResult)
#       -> deerflow.agents.thread_state
#         -> deerflow.agents.__init__
#           -> lead_agent.agent
#             -> subagent_limit_middleware
#               -> deerflow.subagents.executor  <-- circular!
#
# By injecting a mock for deerflow.subagents.executor *before* any test module
# triggers the import, __init__.py's "from .executor import ..." succeeds
# immediately without running the real executor module.
_executor_mock = MagicMock()
_executor_mock.SubagentExecutor = MagicMock
_executor_mock.SubagentResult = MagicMock
_executor_mock.SubagentStatus = MagicMock
_executor_mock.MAX_CONCURRENT_SUBAGENTS = 3
_executor_mock.get_background_task_result = MagicMock()

sys.modules["deerflow.subagents.executor"] = _executor_mock


@pytest.fixture()
def provisioner_module():
    """Load docker/provisioner/app.py as an importable test module.

    Shared by test_provisioner_kubeconfig and test_provisioner_pvc_volumes so
    that any change to the provisioner entry-point path or module name only
    needs to be updated in one place.
    """
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docker" / "provisioner" / "app.py"
    spec = importlib.util.spec_from_file_location("provisioner_app_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Auto-set user context for every test unless marked no_auto_user
# ---------------------------------------------------------------------------
#
# Repository methods read ``user_id`` from a contextvar by default
# (see ``deerflow.runtime.user_context``). Without this fixture, every
# pre-existing persistence test would raise RuntimeError because the
# contextvar is unset. The fixture sets a default test user on every
# test; tests that explicitly want to verify behaviour *without* a user
# context should mark themselves ``@pytest.mark.no_auto_user``.


@pytest.fixture(autouse=True)
def _reset_skill_storage_singleton():
    """Reset the SkillStorage singleton between tests to prevent cross-test contamination."""
    try:
        from deerflow.skills.storage import reset_skill_storage
    except ImportError:
        yield
        return
    reset_skill_storage()
    try:
        yield
    finally:
        reset_skill_storage()


@pytest.fixture(autouse=True)
def _restore_title_config_singleton():
    """Reset ``_title_config`` to its pristine default after every test.

    ``AppConfig.from_file()`` writes the on-disk ``title`` block into the
    module-level singleton (``config/app_config.py`` calls
    ``load_title_config_from_dict``). Any test that loads the real
    ``config.yaml`` therefore leaves the singleton in a state that
    ``test_title_middleware_core_logic.py`` does not expect; that suite
    relies on the pristine ``TitleConfig()`` default (``enabled=True``).
    We restore the default after every test so test files stay
    independent regardless of order.
    """
    try:
        from deerflow.config.title_config import reset_title_config
    except ImportError:
        yield
        return

    try:
        yield
    finally:
        reset_title_config()


@pytest.fixture(autouse=True)
def _auto_user_context(request):
    """Inject a default ``test-user-autouse`` into the contextvar.

    Opt-out via ``@pytest.mark.no_auto_user``. Uses lazy import so that
    tests which don't touch the persistence layer never pay the cost
    of importing runtime.user_context.
    """
    if request.node.get_closest_marker("no_auto_user"):
        yield
        return

    try:
        from deerflow.runtime.user_context import (
            reset_current_user,
            set_current_user,
        )
    except ImportError:
        yield
        return

    user = SimpleNamespace(id="test-user-autouse", email="test@local")
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)
