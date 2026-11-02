"""Default state directory: ``.nova``, with the pre-rename ``.deer-flow`` honoured.

Resolution: NOVA_HOME / DEER_FLOW_HOME env, else ``.nova`` if it exists, else
``.deer-flow`` if it exists (unmigrated install), else ``.nova``. Code never
moves user data; the legacy directory is used in place.
"""

from __future__ import annotations

import pytest

from nova.config.paths import Paths
from nova.config.runtime_paths import default_home, runtime_home


@pytest.fixture
def project(monkeypatch, tmp_path):
    for name in ("NOVA_HOME", "DEER_FLOW_HOME", "DEERFLOW_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("NOVA_PROJECT_ROOT", raising=False)
    return tmp_path


def test_nova_home_env_wins(project, monkeypatch):
    (project / ".deer-flow").mkdir()
    target = project / "elsewhere"
    monkeypatch.setenv("NOVA_HOME", str(target))
    monkeypatch.setenv("DEER_FLOW_HOME", str(project / "legacy-env"))
    assert runtime_home() == target.resolve()
    assert Paths().base_dir == target.resolve()


def test_legacy_deer_flow_home_env_still_honoured(project, monkeypatch):
    target = project / "legacy-env"
    monkeypatch.setenv("DEER_FLOW_HOME", str(target))
    assert runtime_home() == target.resolve()
    assert Paths().base_dir == target.resolve()


def test_only_legacy_dir_exists_is_used_in_place(project):
    (project / ".deer-flow").mkdir()
    assert runtime_home() == project.resolve() / ".deer-flow"
    assert Paths().base_dir == project.resolve() / ".deer-flow"
    assert not (project / ".nova").exists(), "resolution must never create or move data"


def test_neither_exists_defaults_to_nova(project):
    assert runtime_home() == project.resolve() / ".nova"
    assert Paths().base_dir == project.resolve() / ".nova"


def test_both_exist_prefers_nova(project):
    (project / ".deer-flow").mkdir()
    (project / ".nova").mkdir()
    assert default_home(project) == project / ".nova"
