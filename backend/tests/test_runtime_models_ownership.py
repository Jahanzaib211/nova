"""``save_runtime_model_dicts`` must not leave root-owned files on a bind mount.

The gateway's dev image runs as root, and ``$DEER_FLOW_HOME`` is bind-mounted
from the host. Every save therefore produced a ``root:root 0600``
``runtime_models.yaml`` that the host user could not read: ``make doctor``,
the OpenAPI snapshot, and any host-side pytest run logged a PermissionError
and silently ignored the runtime models (observed 2026-09-18). The writer now
hands the file to the owner of its directory when it runs as root.
"""

from __future__ import annotations

import os
import stat

import pytest

from deerflow.config import runtime_models


@pytest.fixture
def chown_calls(monkeypatch):
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(runtime_models.os, "chown", lambda p, uid, gid: calls.append((str(p), uid, gid)))
    return calls


def test_root_writer_hands_file_to_directory_owner(tmp_path, monkeypatch, chown_calls):
    monkeypatch.setattr(runtime_models.os, "geteuid", lambda: 0)
    fake_uid, fake_gid = 1000, 1000
    real_stat = os.stat

    def dir_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if str(path) == str(tmp_path):
            return os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, fake_uid, fake_gid, 0, 0, 0, 0))
        return result

    monkeypatch.setattr(runtime_models.os, "stat", dir_stat)
    target = tmp_path / "runtime_models.yaml"

    runtime_models.save_runtime_model_dicts([{"name": "m", "model": "x"}], target)

    assert chown_calls == [(str(target), fake_uid, fake_gid)]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600, "must stay private: it can hold API keys"
    assert runtime_models.load_runtime_model_dicts(target) == [{"name": "m", "model": "x"}]


def test_root_writer_leaves_root_owned_directory_alone(tmp_path, monkeypatch, chown_calls):
    """A directory owned by root (no bind mount) needs no hand-off."""
    monkeypatch.setattr(runtime_models.os, "geteuid", lambda: 0)
    real_stat = os.stat
    monkeypatch.setattr(
        runtime_models.os,
        "stat",
        lambda path, *a, **k: os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0)) if str(path) == str(tmp_path) else real_stat(path, *a, **k),
    )
    runtime_models.save_runtime_model_dicts([], tmp_path / "runtime_models.yaml")
    assert chown_calls == []


def test_non_root_writer_never_chowns(tmp_path, monkeypatch, chown_calls):
    monkeypatch.setattr(runtime_models.os, "geteuid", lambda: 1000)
    runtime_models.save_runtime_model_dicts([], tmp_path / "runtime_models.yaml")
    assert chown_calls == []


def test_chown_failure_does_not_lose_the_write(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_models.os, "geteuid", lambda: 0)
    real_stat = os.stat
    monkeypatch.setattr(
        runtime_models.os,
        "stat",
        lambda path, *a, **k: os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, 1000, 1000, 0, 0, 0, 0)) if str(path) == str(tmp_path) else real_stat(path, *a, **k),
    )

    def boom(*_):
        raise PermissionError("chown denied")

    monkeypatch.setattr(runtime_models.os, "chown", boom)
    target = runtime_models.save_runtime_model_dicts([{"name": "m"}], tmp_path / "runtime_models.yaml")
    assert runtime_models.load_runtime_model_dicts(target) == [{"name": "m"}]
