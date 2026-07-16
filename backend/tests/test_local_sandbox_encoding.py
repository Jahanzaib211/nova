import builtins
from types import SimpleNamespace  # noqa: F401 — kept for parity with sibling suites

import pytest

import deerflow.sandbox.local.local_sandbox as local_sandbox
from deerflow.execution.testing import FakeExecutionKernel
from deerflow.services.container import service_container


@pytest.fixture(autouse=True)
def _isolated_service_container():
    yield
    service_container.reset()


def _install_capturing_kernel(calls: list) -> FakeExecutionKernel:
    """Capture each ExecutionRequest as (argv, env, timeout) and return 'ok'."""

    def handler(request):
        calls.append((list(request.argv), request.env, request.limits.timeout))
        return (0, "ok", "")

    fake = FakeExecutionKernel(handler)
    service_container.override(execution_kernel=fake)
    return fake


def _open(base, file, mode="r", *args, **kwargs):
    if "b" in mode:
        return base(file, mode, *args, **kwargs)
    return base(file, mode, *args, encoding=kwargs.pop("encoding", "gbk"), **kwargs)


def test_read_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "\u201cutf8\u201d"
    path.write_text(text, encoding="utf-8")
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    assert local_sandbox.LocalSandbox("t").read_file(str(path)) == text


def test_write_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "emoji \U0001f600"
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    local_sandbox.LocalSandbox("t").write_file(str(path), text)

    assert path.read_text(encoding="utf-8") == text


def test_get_shell_prefers_posix_shell_from_path_before_windows_fallback(monkeypatch):
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_find_first_available_shell", lambda candidates: r"C:\Program Files\Git\bin\sh.exe" if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh") else None)

    assert local_sandbox.LocalSandbox._get_shell() == r"C:\Program Files\Git\bin\sh.exe"


def test_get_shell_uses_powershell_fallback_on_windows(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_find(candidates: tuple[str, ...]) -> str | None:
        calls.append(candidates)
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_find_first_available_shell", fake_find)

    assert local_sandbox.LocalSandbox._get_shell() == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert calls[1] == (
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "cmd.exe",
    )


def test_get_shell_uses_cmd_as_last_windows_fallback(monkeypatch):
    def fake_find(candidates: tuple[str, ...]) -> str | None:
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\cmd.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_find_first_available_shell", fake_find)

    assert local_sandbox.LocalSandbox._get_shell() == r"C:\Windows\System32\cmd.exe"


def test_execute_command_uses_powershell_command_mode_on_windows(monkeypatch):
    calls: list = []

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    _install_capturing_kernel(calls)

    output = local_sandbox.LocalSandbox("t").execute_command("Write-Output hello")

    assert output == "ok"
    assert calls == [
        (
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-Command",
                "Write-Output hello",
            ],
            None,
            600,
        )
    ]


def test_execute_command_uses_posix_shell_command_mode_on_windows(monkeypatch):
    calls: list = []

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Program Files\Git\bin"})
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Program Files\Git\bin\sh.exe"))
    _install_capturing_kernel(calls)

    output = local_sandbox.LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert calls == [
        (
            [r"C:\Program Files\Git\bin\sh.exe", "-c", "echo hello"],
            {
                "PATH": r"C:\Program Files\Git\bin",
                "MSYS_NO_PATHCONV": "1",
                "MSYS2_ARG_CONV_EXCL": "*",
            },
            600,
        )
    ]


def test_execute_command_does_not_set_msys_env_for_non_msys_posix_shell_on_windows(monkeypatch):
    calls: list = []

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\tools\busybox\sh.exe"))
    _install_capturing_kernel(calls)

    output = local_sandbox.LocalSandbox("t").execute_command("echo /mnt/skills/demo")

    assert output == "ok"
    assert calls[0][1] is None


def test_execute_command_uses_cmd_command_mode_on_windows(monkeypatch):
    calls: list = []

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\cmd.exe"))
    _install_capturing_kernel(calls)

    output = local_sandbox.LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert calls == [
        (
            [r"C:\Windows\System32\cmd.exe", "/c", "echo hello"],
            None,
            600,
        )
    ]
