"""The one-click live preview serves plain-HTML projects too.

The pipeline only looked for a ``package.json``. A vanilla HTML/JS app (the
todo app an agent built with subagents) returned ``no_project``, so the
Browser tab stayed in its file-viewer fallback -- no address bar, no refresh,
no open-in-new-tab -- unless an agent happened to have a server running, and
every gateway reload stopped that server with the sandbox.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.sandbox import dev_server


class _Sandbox:
    def __init__(self, package_json: str = "", index_html: str = "") -> None:
        self.package_json = package_json
        self.index_html = index_html
        self.commands: list[str] = []

    def execute_command(self, cmd: str) -> str:
        self.commands.append(cmd)
        if "-name package.json" in cmd:
            return self.package_json
        if "-name index.html" in cmd:
            return self.index_html
        if "node_modules ] && echo yes" in cmd:
            return "yes"
        return ""


@pytest.fixture
def started(monkeypatch):
    calls: list[dict] = []

    async def _start(thread_id, cwd, command="npm run dev", *, sandbox=None, label="app", container_port=4100):
        calls.append({"cwd": cwd, "command": command, "label": label, "container_port": container_port})
        handle = dev_server.DevServerHandle(thread_id=thread_id, port=container_port, cwd=cwd, command=command, label=label)
        handle.status = "starting"
        return handle

    async def _no_verify(*a, **k):
        return None

    monkeypatch.setattr(dev_server, "start_dev_server", _start)
    monkeypatch.setattr(dev_server, "_auto_verify_preview", _no_verify)
    monkeypatch.setattr(dev_server, "allocate_container_port", lambda *a, **k: 4100)
    monkeypatch.setattr(dev_server, "get_dev_server", lambda *a, **k: None)
    monkeypatch.setattr(dev_server, "_append_devlog_to_sandbox_log", lambda *a, **k: None)
    return calls


def _run(sandbox: _Sandbox) -> dict:
    async def go():
        result = await dev_server.run_preview_pipeline("t-static", sandbox)
        await asyncio.sleep(0)
        return result

    return asyncio.run(go())


def test_a_plain_html_project_is_served_statically(started):
    result = _run(_Sandbox(index_html="/mnt/user-data/workspace/index.html\n"))
    assert result["started"] is True
    assert started == [{"cwd": "/mnt/user-data/workspace", "command": "python3 -m http.server 4100 --bind 0.0.0.0", "label": "app", "container_port": 4100}]


def test_a_nested_html_project_is_served_from_its_own_directory(started):
    _run(_Sandbox(index_html="/mnt/user-data/workspace/todo-app/index.html\n"))
    assert started[0]["cwd"] == "/mnt/user-data/workspace/todo-app"


def test_a_node_project_still_wins_over_a_stray_html_file(started):
    _run(_Sandbox(package_json="/mnt/user-data/workspace/web/package.json\n", index_html="/mnt/user-data/workspace/index.html\n"))
    assert started[0]["command"] == "npm run dev"
    assert started[0]["cwd"] == "/mnt/user-data/workspace/web"


def test_nothing_runnable_is_still_reported(started):
    result = _run(_Sandbox())
    assert result == {"started": False, "reason": "no_project"}
    assert started == []
