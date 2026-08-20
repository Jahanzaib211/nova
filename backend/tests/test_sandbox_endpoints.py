"""Tests for sandbox observation endpoints that previously had no coverage.

Covers:
- GET /api/sandbox/logs   — SSE log tailing
- GET /api/sandbox/status — tool label
- GET /api/sandbox/file   — raw file content
- GET /api/sandbox/files  — file listing
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.routers import sandbox as sandbox_router


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    """Bypass auth for all tests in this module."""
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")


@pytest.fixture
def sandbox_tree(tmp_path, monkeypatch):
    """Build a minimal on-disk user-data tree and mock the ownership check."""
    user_data = tmp_path / "user-data"
    workspace = user_data / "workspace"
    outputs = user_data / "outputs"
    uploads = user_data / "uploads"
    for d in (workspace, outputs, uploads):
        d.mkdir(parents=True)

    (workspace / "hello.txt").write_text("hello world", encoding="utf-8")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "nested.txt").write_text("nested", encoding="utf-8")
    (outputs / "result.md").write_text("# Result", encoding="utf-8")
    (uploads / "data.csv").write_text("a,b,c", encoding="utf-8")

    # Create sandbox log file
    log_path = tmp_path / "sandbox.log"
    log_path.write_text("line 1\nline 2\n", encoding="utf-8")

    # Create sandbox status file
    status_path = tmp_path / "sandbox_status.json"
    status_path.write_text(json.dumps({"tool": "bash", "label": "is using Terminal"}), encoding="utf-8")

    fake_paths = SimpleNamespace(
        sandbox_work_dir=lambda tid, user_id=None: workspace,
        sandbox_outputs_dir=lambda tid, user_id=None: outputs,
        sandbox_uploads_dir=lambda tid, user_id=None: uploads,
        thread_dir=lambda tid, user_id=None: tmp_path,
        resolve_virtual_path=lambda tid, path, user_id=None: workspace / path.removeprefix("/mnt/user-data/workspace/"),
    )
    monkeypatch.setattr(sandbox_router, "get_paths", lambda: fake_paths)
    monkeypatch.setattr(sandbox_router, "get_effective_user_id", lambda: "u1")
    monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)
    return tmp_path


class TestSandboxStatus:
    def test_returns_tool_label(self, sandbox_tree):
        result = asyncio.run(sandbox_router.get_sandbox_status("t1", request=None))
        assert result["tool"] == "bash"
        assert result["label"] == "is using Terminal"

    def test_returns_idle_when_no_status_file(self, sandbox_tree):
        (sandbox_tree / "sandbox_status.json").unlink(missing_ok=True)
        result = asyncio.run(sandbox_router.get_sandbox_status("t1", request=None))
        assert result["tool"] is None
        assert result["label"] == "idle"

    def test_ownership_check_blocks(self, sandbox_tree, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: False)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sandbox_router.get_sandbox_status("t1", request=None))
        assert exc_info.value.status_code == 404


class TestSandboxFile:
    def test_returns_file_content(self, sandbox_tree):
        result = asyncio.run(sandbox_router.get_sandbox_file("t1", "/mnt/user-data/workspace/hello.txt", request=None))
        assert result["content"] == "hello world"
        assert result["exists"] is True
        assert result["size"] > 0

    def test_returns_empty_for_nonexistent(self, sandbox_tree):
        result = asyncio.run(sandbox_router.get_sandbox_file("t1", "/mnt/user-data/workspace/nope.txt", request=None))
        assert result["exists"] is False

    def test_rejects_outside_user_data(self, sandbox_tree):
        result = asyncio.run(sandbox_router.get_sandbox_file("t1", "/etc/passwd", request=None))
        assert result["exists"] is False

    def test_ownership_check_blocks(self, sandbox_tree, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: False)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sandbox_router.get_sandbox_file("t1", "/mnt/user-data/workspace/hello.txt", request=None))
        assert exc_info.value.status_code == 404


class TestSandboxFiles:
    def test_lists_files(self, sandbox_tree):
        result = asyncio.run(sandbox_router.list_sandbox_files("t1", request=None))
        names = {f["name"] for f in result["files"]}
        assert "hello.txt" in names
        assert "result.md" in names

    def test_ownership_check_blocks(self, sandbox_tree, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: False)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sandbox_router.list_sandbox_files("t1", request=None))
        assert exc_info.value.status_code == 404


class TestSandboxLogs:
    def test_ownership_check_blocks(self, sandbox_tree, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: False)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sandbox_router.stream_sandbox_logs("t1", request=None))
        assert exc_info.value.status_code == 404
