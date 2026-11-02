"""The legacy ``deerflow`` import name is an alias of ``nova``, never a copy.

Nova was forked from DeerFlow and the harness used to live under ``deerflow``.
Existing ``config.yaml`` files carry ``use: deerflow.x:Y`` paths and out-of-tree
scripts still ``import deerflow``; both must keep working, and must reach the
very same module objects -- a second copy of a module would mean a second
sandbox provider cache, a second tool registry, and so on.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap

import pytest


def test_deerflow_submodule_is_the_nova_module():
    import deerflow.sandbox.sandbox_provider as a
    import nova.sandbox.sandbox_provider as b

    assert a is b


def test_module_singletons_are_shared():
    import deerflow.sandbox.sandbox_provider as legacy
    import nova.sandbox.sandbox_provider as current

    assert legacy.get_sandbox_provider is current.get_sandbox_provider
    assert getattr(legacy, "_default_sandbox_provider", None) is getattr(current, "_default_sandbox_provider", None)


def test_alias_keeps_the_real_spec():
    import deerflow.config.app_config as legacy

    assert legacy.__name__ == "nova.config.app_config"
    assert legacy.__spec__.name == "nova.config.app_config"


def test_plain_module_is_not_turned_into_a_package():
    import deerflow.client as legacy

    assert not hasattr(legacy, "__path__")


def test_attribute_access_on_bare_alias_package():
    import deerflow
    import nova.reflection

    assert deerflow.reflection is nova.reflection
    with pytest.raises(AttributeError):
        deerflow.definitely_not_a_module  # noqa: B018


def test_legacy_first_import_order_in_a_fresh_interpreter():
    """Importing via the legacy name FIRST must not build a second copy either."""
    code = textwrap.dedent(
        """
        import os, sys
        os.environ.setdefault("PYTEST_CURRENT_TEST", "1")
        import deerflow.sandbox.sandbox_provider as a
        import nova.sandbox.sandbox_provider as b
        assert a is b, "duplicate module"
        import deerflow.reflection.resolvers as r1
        import nova.reflection.resolvers as r2
        assert r1 is r2
        for name, mod in list(sys.modules.items()):
            if name.startswith("deerflow."):
                twin = sys.modules.get("nova." + name[len("deerflow."):])
                assert twin is mod, name
        print("ok")
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")


@pytest.mark.parametrize(
    "path",
    [
        "deerflow.sandbox.local:LocalSandboxProvider",
        "deerflow.sandbox.tools:bash_tool",
        "deerflow.models.vllm_provider:VllmChatModel",
    ],
)
def test_resolve_variable_accepts_legacy_paths(path):
    from nova.reflection import resolve_variable

    legacy = resolve_variable(path)
    current = resolve_variable("nova." + path[len("deerflow.") :])
    assert legacy is current


def test_resolve_class_accepts_legacy_paths():
    from nova.reflection import resolve_class
    from nova.sandbox.sandbox_provider import SandboxProvider

    cls = resolve_class("deerflow.sandbox.local:LocalSandboxProvider", SandboxProvider)
    assert cls is importlib.import_module("nova.sandbox.local").LocalSandboxProvider
