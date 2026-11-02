"""Per-thread browser state shared by the tools (harness) and the gateway API.

Files live in ``{thread_dir}/browser/`` — beside ``user-data/``, not inside it,
so nothing here is visible to the sandbox, the Files tab or a workspace zip:

    settings.json   {"recording": bool, "profile": name|null}
    attention.json  {"kind", "url", "title", "reason", "at", "resolved_at"}

Both sides address the directory through ``nova.config.paths``; the tools
derive it from the thread's host ``outputs_path`` (``…/user-data/outputs``),
the gateway from ``get_paths().thread_dir(thread_id, user_id=…)``.

In-process state (which threads already had their default profile attached)
is a plain dict: it only optimises the first attach, so losing it on a
restart merely re-applies an idempotent cookie injection.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_attached: dict[str, str | None] = {}  # thread_id -> profile name applied on first attach (or None)
_MAX_ATTACHED = 4096


def thread_dir_from_outputs(outputs_path: str | Path | None) -> Path | None:
    if not outputs_path:
        return None
    p = Path(outputs_path)
    # …/threads/{tid}/user-data/outputs → …/threads/{tid}
    return p.parent.parent if p.name == "outputs" and p.parent.name == "user-data" else None


def state_dir(thread_dir: Path) -> Path:
    return Path(thread_dir) / "browser"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def read_settings(thread_dir: Path | None) -> dict[str, Any]:
    return _read(state_dir(thread_dir) / "settings.json") if thread_dir else {}


def update_settings(thread_dir: Path, **changes: Any) -> dict[str, Any]:
    with _lock:
        cur = read_settings(thread_dir)
        cur.update(changes)
        _write(state_dir(thread_dir) / "settings.json", cur)
        return cur


def read_attention(thread_dir: Path | None) -> dict[str, Any] | None:
    if not thread_dir:
        return None
    data = _read(state_dir(thread_dir) / "attention.json")
    return data or None


def raise_attention(thread_dir: Path | None, *, kind: str, url: str, title: str = "", reason: str = "") -> None:
    """Ask the user (Browser tab) to act in the live view. URL is stored without its query string."""
    if not thread_dir:
        return
    from nova.browser.recording import redact_url

    _write(state_dir(thread_dir) / "attention.json", {"kind": kind, "url": redact_url(url), "title": title[:200], "reason": reason[:300], "at": time.time(), "resolved_at": None})


def resolve_attention(thread_dir: Path) -> dict[str, Any] | None:
    with _lock:
        cur = read_attention(thread_dir)
        if cur is None:
            return None
        cur["resolved_at"] = time.time()
        _write(state_dir(thread_dir) / "attention.json", cur)
        return cur


def clear_attention(thread_dir: Path | None) -> None:
    if thread_dir:
        try:
            (state_dir(thread_dir) / "attention.json").unlink()
        except FileNotFoundError:
            pass


def first_attach(thread_id: str) -> bool:
    with _lock:
        return thread_id not in _attached


def mark_attached(thread_id: str, profile: str | None) -> None:
    with _lock:
        if len(_attached) >= _MAX_ATTACHED:
            _attached.pop(next(iter(_attached)))
        _attached[thread_id] = profile


def forget(thread_id: str) -> None:
    with _lock:
        _attached.pop(thread_id, None)
