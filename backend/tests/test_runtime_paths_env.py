"""Tests for runtime path helpers added in v4.

Covers:
- ``project_root()`` existing behaviour is unchanged.
- ``set_project_root_from_cwd()`` is idempotent and honours ``overwrite``.
- ``in_container()`` returns a bool and does not raise on non-Linux or
  restricted environments.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_canonical_project_root(monkeypatch):
    # These tests exercise the legacy name; the canonical one would shadow it.
    # setenv first so monkeypatch records the original state and removes the
    # NOVA_ name set_project_root_from_cwd() writes, instead of leaking it.
    monkeypatch.setenv("NOVA_PROJECT_ROOT", "")
    monkeypatch.delenv("NOVA_PROJECT_ROOT")


def test_project_root_prefers_nova_name(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(legacy))
    monkeypatch.setenv("NOVA_PROJECT_ROOT", str(tmp_path))
    from nova.config.runtime_paths import project_root

    assert project_root() == tmp_path.resolve()


def test_project_root_resolves_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(tmp_path))
    from nova.config.runtime_paths import project_root

    assert project_root() == tmp_path.resolve()


def test_project_root_falls_back_to_cwd_when_env_unset(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_PROJECT_ROOT", raising=False)
    cwd = Path.cwd().resolve()
    from nova.config.runtime_paths import project_root

    assert project_root() == cwd


def test_project_root_raises_on_missing_env_path(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", "/this/path/does/not/exist/anywhere")
    from nova.config.runtime_paths import project_root

    with pytest.raises(ValueError, match="does not exist"):
        project_root()


def test_set_project_root_from_cwd_is_idempotent(monkeypatch, tmp_path):
    """If env var is already set, the helper does NOT overwrite (default)."""
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(tmp_path))
    from nova.config.runtime_paths import set_project_root_from_cwd

    result = set_project_root_from_cwd()
    assert result == tmp_path.resolve()
    assert os.environ["DEER_FLOW_PROJECT_ROOT"] == str(tmp_path)


def test_set_project_root_from_cwd_sets_env_when_unset(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_PROJECT_ROOT", raising=False)
    from nova.config.runtime_paths import set_project_root_from_cwd

    result = set_project_root_from_cwd()
    expected = Path.cwd().resolve()
    assert result == expected
    assert os.environ["DEER_FLOW_PROJECT_ROOT"] == str(expected)


def test_set_project_root_from_cwd_overwrite_true(monkeypatch, tmp_path):
    """``overwrite=True`` replaces an existing env-var value with cwd."""
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(tmp_path))
    from nova.config.runtime_paths import set_project_root_from_cwd

    result = set_project_root_from_cwd(overwrite=True)
    expected = Path.cwd().resolve()
    assert result == expected
    assert os.environ["DEER_FLOW_PROJECT_ROOT"] == str(expected)
    assert os.environ["NOVA_PROJECT_ROOT"] == str(expected)


def test_set_project_root_from_cwd_returns_none_for_invalid_cwd(monkeypatch):
    """If cwd is invalid, the helper returns None and does NOT set env."""
    monkeypatch.delenv("DEER_FLOW_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("os.getcwd", lambda: "/this/path/does/not/exist")

    from nova.config.runtime_paths import set_project_root_from_cwd

    # The helper falls through to None because Path.cwd().resolve() will
    # raise or return a non-existent path. Either way, it must not
    # silently set a bad env var.
    try:
        result = set_project_root_from_cwd()
    except FileNotFoundError:
        # Acceptable: cwd resolution is allowed to raise when cwd is bad
        return
    assert result is None or result.exists(), "if it returned a value, it must exist on disk"
    # And the env var must not point at a non-existent path
    if "DEER_FLOW_PROJECT_ROOT" in os.environ:
        assert Path(os.environ["DEER_FLOW_PROJECT_ROOT"]).exists()


def test_in_container_returns_bool():
    from nova.config.runtime_paths import in_container

    result = in_container()
    assert isinstance(result, bool)
    # Should not raise under any platform
    assert result in (True, False)
