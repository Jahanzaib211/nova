"""Pin every ``client.browser_page.*`` kwarg against the installed SDK.

This is the test that would have caught the bug this file exists because of.

``browser_eval`` called ``evaluate(script=...)`` while the installed
``agent-sandbox`` SDK declares ``evaluate(*, expression: str)``, and
``browser_input`` called ``fill(selector=..., value=...)`` against
``fill(*, text: str, selector=None, ...)``. Both raise ``TypeError`` *before
any HTTP request leaves the process*, so neither tool had ever worked on any
sandbox image — and because every browser tool funnels exceptions into
``return f"Error: {e}"``, the failure was invisible in normal use.

Rather than hardcode the expected kwargs (which drifts the moment someone
adds a call), this walks the AST of ``workspace_tools.py``, extracts the
keyword arguments of every ``client.browser_page.<method>(...)`` call, and
validates them against ``inspect.signature`` of the real installed client.
New call sites are covered automatically.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

agent_sandbox = pytest.importorskip("agent_sandbox", reason="agent-sandbox SDK not installed")

import deerflow.tools.builtins.workspace_tools as workspace_tools  # noqa: E402


def _browser_page_calls() -> list[tuple[str, set[str], int]]:
    """Every ``client.browser_page.<method>(**kwargs)`` call in workspace_tools.

    Returns (method_name, keyword_names, lineno) triples.
    """
    source = Path(inspect.getfile(workspace_tools)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls: list[tuple[str, set[str], int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `<anything>.browser_page.<method>`
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "browser_page"):
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        calls.append((func.attr, kwargs, node.lineno))

    return calls


def _sdk_client_class():
    from agent_sandbox.browser_page.client import BrowserPageClient

    return BrowserPageClient


class TestBrowserPageCallContract:
    def test_call_sites_are_discovered(self) -> None:
        """Guard the guard: if the AST walk silently matches nothing, every
        assertion below would vacuously pass."""
        calls = _browser_page_calls()
        methods = {name for name, _, _ in calls}
        assert calls, "no client.browser_page.* calls found — the AST matcher is broken"
        # The four the agent actually drives a page with.
        assert {"navigate", "click", "fill", "evaluate"} <= methods

    def test_every_method_exists_on_the_sdk(self) -> None:
        client_cls = _sdk_client_class()
        for method, _, lineno in _browser_page_calls():
            assert hasattr(client_cls, method), f"workspace_tools.py:{lineno} calls browser_page.{method}(), which does not exist on {client_cls.__name__}"

    def test_every_kwarg_exists_on_the_sdk_signature(self) -> None:
        """The regression itself: `script=` / `value=` were not real parameters."""
        client_cls = _sdk_client_class()
        failures: list[str] = []

        for method, kwargs, lineno in _browser_page_calls():
            impl = getattr(client_cls, method, None)
            if impl is None:
                continue
            params = inspect.signature(impl).parameters
            accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
            if accepts_var_kw:
                continue
            for kw in sorted(kwargs):
                if kw not in params:
                    failures.append(f"workspace_tools.py:{lineno}: browser_page.{method}({kw}=...) — valid parameters are {sorted(p for p in params if p != 'self')}")

        assert not failures, "SDK signature drift:\n" + "\n".join(failures)

    def test_required_params_are_supplied(self) -> None:
        """`fill` requires `text`; omitting it is as fatal as misspelling it."""
        client_cls = _sdk_client_class()
        failures: list[str] = []

        for method, kwargs, lineno in _browser_page_calls():
            impl = getattr(client_cls, method, None)
            if impl is None:
                continue
            for name, param in inspect.signature(impl).parameters.items():
                if name == "self" or param.kind is inspect.Parameter.VAR_KEYWORD:
                    continue
                # Keyword-only with no default == genuinely required.
                if param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty and name not in kwargs:
                    failures.append(f"workspace_tools.py:{lineno}: browser_page.{method}() omits required parameter {name!r}")

        assert not failures, "missing required SDK parameters:\n" + "\n".join(failures)


class TestKnownSignatures:
    """Explicit assertions on the two that broke, so the intent survives even
    if the AST matcher is ever refactored away."""

    def test_evaluate_takes_expression_not_script(self) -> None:
        params = inspect.signature(_sdk_client_class().evaluate).parameters
        assert "expression" in params
        assert "script" not in params

    def test_fill_takes_text_not_value(self) -> None:
        params = inspect.signature(_sdk_client_class().fill).parameters
        assert "text" in params
        assert "value" not in params
