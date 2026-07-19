"""Tests for the shared atomic-write helper (2026-07 audit C4)."""

from __future__ import annotations

import stat

import pytest

from deerflow.utils.atomic_write import atomic_write_text


def test_writes_new_file(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'


def test_replaces_existing_file_content(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("old content", encoding="utf-8")
    atomic_write_text(target, "new content")
    assert target.read_text(encoding="utf-8") == "new content"


def test_no_temp_file_left_behind_on_success(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, "hello")
    leftover = [p for p in tmp_path.iterdir() if p != target]
    assert leftover == []


def test_original_file_untouched_when_write_raises(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    original = '{"existing": true}'
    target.write_text(original, encoding="utf-8")

    import tempfile as tempfile_module

    real_named_temp_file = tempfile_module.NamedTemporaryFile

    class ExplodingFile:
        name = str(tmp_path / "fake.tmp")

        def write(self, *_args, **_kwargs):
            raise RuntimeError("simulated crash mid-write")

        def close(self):
            pass

    def fake_named_temp_file(*_args, **_kwargs):
        # Create a real temp file on disk (so the cleanup path has
        # something real to unlink) but make .write() explode.
        real = real_named_temp_file(
            mode="w", dir=tmp_path, suffix=".tmp", delete=False, encoding="utf-8"
        )
        real.write = ExplodingFile.write.__get__(real)
        return real

    monkeypatch.setattr("deerflow.utils.atomic_write.tempfile.NamedTemporaryFile", fake_named_temp_file)

    with pytest.raises(RuntimeError, match="simulated crash mid-write"):
        atomic_write_text(target, "new content that never lands")

    assert target.read_text(encoding="utf-8") == original
    leftover = [p for p in tmp_path.iterdir() if p != target]
    assert leftover == [], f"temp file(s) leaked: {leftover}"


def test_applies_mode_before_replace(tmp_path):
    target = tmp_path / "secret.json"
    atomic_write_text(target, '{"token": "x"}', mode=0o600)
    actual_mode = stat.S_IMODE(target.stat().st_mode)
    assert actual_mode == 0o600
