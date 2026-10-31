"""Regression tests for the environment the provisioner injects into sandbox Pods.

Why this exists
---------------
Chromium inside a sandbox image can only start if its *own* sandbox works.
The backend's local-Docker path hardens the container (``--cap-drop ALL`` +
``no-new-privileges``) and compensates by setting ``BROWSER_NO_SANDBOX``
(the image's ``/opt/gem/run.sh`` appends it to ``BROWSER_EXTRA_ARGS``).

The provisioner path cannot do that: the backend's ``sandbox.environment``
never reaches this service, because the Pod spec is built here. The Pod spec
built by default leaves Chromium's inner sandbox working (no capability drop,
``allow_privilege_escalation=True``), so nothing has to be injected — until a
cluster forbids privilege escalation, at which point the browser dies exactly
as it died in the hardened local container.

``BROWSER_NO_SANDBOX`` on the provisioner is therefore the escape hatch for
that case, and these tests pin both halves: it is off by default, and when it
is set it reaches the Pod.
"""

import importlib.util
from pathlib import Path


def _load_provisioner_module(name: str):
    """Import docker/provisioner/app.py fresh, so module-level env reads re-run."""
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docker" / "provisioner" / "app.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBuildPodEnv:
    """_build_pod_env: what reaches the sandbox container."""

    def test_no_env_by_default(self, provisioner_module):
        """Nothing is injected when the operator has not asked for it."""
        provisioner_module.BROWSER_NO_SANDBOX = ""
        assert provisioner_module._build_pod_env() == []

    def test_browser_no_sandbox_is_passed_through(self, provisioner_module):
        """The knob reaches the Pod as the variable the image's run.sh reads."""
        provisioner_module.BROWSER_NO_SANDBOX = "--no-sandbox"
        env = provisioner_module._build_pod_env()
        assert len(env) == 1
        assert env[0].name == "BROWSER_NO_SANDBOX"
        assert env[0].value == "--no-sandbox"


class TestBuildPodEnvWiring:
    """The Pod the provisioner actually creates carries the environment."""

    def test_pod_container_has_no_env_by_default(self, provisioner_module):
        provisioner_module.BROWSER_NO_SANDBOX = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert not pod.spec.containers[0].env

    def test_pod_container_carries_browser_no_sandbox(self, provisioner_module):
        provisioner_module.BROWSER_NO_SANDBOX = "--no-sandbox"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        env = {item.name: item.value for item in pod.spec.containers[0].env}
        assert env == {"BROWSER_NO_SANDBOX": "--no-sandbox"}


class TestProvisionerEnvDefault:
    """The knob is read from the provisioner's own environment."""

    def test_reads_browser_no_sandbox_from_the_environment(self, monkeypatch):
        """A fresh import of the module picks the operator's value up."""
        monkeypatch.setenv("BROWSER_NO_SANDBOX", "--no-sandbox")
        module = _load_provisioner_module("provisioner_app_env_test")
        assert module.BROWSER_NO_SANDBOX == "--no-sandbox"

    def test_blank_value_is_treated_as_unset(self, monkeypatch):
        """A whitespace-only value must not become a stray env entry."""
        monkeypatch.setenv("BROWSER_NO_SANDBOX", "   ")
        module = _load_provisioner_module("provisioner_app_env_blank_test")
        assert module.BROWSER_NO_SANDBOX == ""
        assert module._build_pod_env() == []
