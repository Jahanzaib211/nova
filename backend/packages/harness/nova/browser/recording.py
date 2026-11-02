"""Browser session recording: sampled screencast frames + an action log.

Layout (inside the thread's outputs, in a dot-directory so the Files tab and
the workspace zip skip it)::

    outputs/.recordings/<session_id>/manifest.json
    outputs/.recordings/<session_id>/f000001.jpg …

One session spans consecutive browser tool calls in a thread until it has been
idle for ``SESSION_IDLE_S``. Frames come from CDP ``Page.startScreencast`` while
a tool call runs, throttled to ``browser.recording.fps``, plus one still after
each call so a static page still gets a frame per step.

Cost: at the defaults (2 fps, JPEG q55, ≤1024 px wide) a frame is ~30-80 KB,
so a busy minute of browsing is ~4-8 MB; the per-session caps
(``max_frames``/``max_bytes``) stop capture rather than grow, and only the
newest ``keep_sessions`` sessions per thread are kept.

Manifests never contain secrets: typed text is replaced by its length, eval
scripts by their length, and URL query values whose key looks like a
credential are redacted. Cookies are never touched by the recorder.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from nova.config.browser_config import BrowserRecordingConfig

RECORDINGS_DIRNAME = ".recordings"
SESSION_IDLE_S = 30 * 60
_SESSION_RE = re.compile(r"^rec-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_FRAME_RE = re.compile(r"^f[0-9]{6}\.jpg$")
_SECRET_KEY_RE = re.compile(r"(token|key|secret|pass|pwd|auth|session|sid|sig|code|credential|jwt)", re.I)
_lock = threading.Lock()
_current: dict[str, tuple[str, float]] = {}  # recordings dir → (session id, last activity)


def redact_url(url: str) -> str:
    try:
        p = urlparse(url)
    except ValueError:
        return ""
    if not p.query and not p.username and not p.password:
        return url
    q = [(k, "REDACTED" if _SECRET_KEY_RE.search(k) else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    netloc = p.hostname or ""
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, urlencode(q), ""))


def valid_session_id(sid: str) -> bool:
    return bool(_SESSION_RE.match(sid or ""))


def valid_frame_name(name: str) -> bool:
    return bool(_FRAME_RE.match(name or ""))


def recordings_dir(outputs_dir: Path) -> Path:
    return Path(outputs_dir) / RECORDINGS_DIRNAME


def list_sessions(outputs_dir: Path) -> list[dict[str, Any]]:
    root = recordings_dir(outputs_dir)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        if not (d.is_dir() and valid_session_id(d.name)):
            continue
        m = read_manifest(outputs_dir, d.name)
        if m:
            out.append({k: m.get(k) for k in ("session_id", "started_at", "updated_at", "frame_count", "action_count", "bytes", "truncated")})
    return out


def read_manifest(outputs_dir: Path, session_id: str) -> dict[str, Any] | None:
    if not valid_session_id(session_id):
        return None
    try:
        data = json.loads((recordings_dir(outputs_dir) / session_id / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def frame_path(outputs_dir: Path, session_id: str, frame: str) -> Path | None:
    if not (valid_session_id(session_id) and valid_frame_name(frame)):
        return None
    p = recordings_dir(outputs_dir) / session_id / frame
    return p if p.is_file() else None


def sanitize_action(tool: str, detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": tool}
    for k, v in (detail or {}).items():
        if k == "url":
            out["url"] = redact_url(str(v))[:500]
        elif k == "selector":
            out["selector"] = str(v)[:300]
        elif k in ("text", "script"):
            out[f"{k}_chars"] = len(str(v))
        elif isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str) and k in ("name", "path", "kind"):
            out[k] = v[:200]
    return out


class Recorder:
    """Records one tool call into the thread's current session."""

    def __init__(self, outputs_dir: Path, cfg: BrowserRecordingConfig) -> None:
        self.cfg = cfg
        self.root = recordings_dir(outputs_dir)
        self._frames: list[dict[str, Any]] = []
        self._last_frame_t = 0.0
        self._cdp = None
        self.session_id, self.dir, self.manifest = self._open_session()

    def _open_session(self) -> tuple[str, Path, dict[str, Any]]:
        key = str(self.root)
        now = time.time()
        with _lock:
            cur = _current.get(key)
            if cur and now - cur[1] < SESSION_IDLE_S and (self.root / cur[0] / "manifest.json").is_file():
                sid = cur[0]
            else:
                sid = time.strftime("rec-%Y%m%d-%H%M%S-", time.gmtime(now)) + secrets.token_hex(3)
            _current[key] = (sid, now)
        d = self.root / sid
        d.mkdir(parents=True, exist_ok=True)
        manifest = read_manifest(self.root.parent, sid) or {
            "version": 1,
            "session_id": sid,
            "started_at": now,
            "updated_at": now,
            "fps": self.cfg.fps,
            "frame_count": 0,
            "action_count": 0,
            "bytes": 0,
            "truncated": False,
            "frames": [],
            "actions": [],
        }
        if not (self.root / sid / "manifest.json").is_file():
            self._prune()
        return sid, d, manifest

    def _prune(self) -> None:
        sessions = sorted((p for p in self.root.iterdir() if p.is_dir() and valid_session_id(p.name)), key=lambda p: p.name)
        for old in sessions[: max(0, len(sessions) - self.cfg.keep_sessions + 1)]:
            shutil.rmtree(old, ignore_errors=True)

    # frames ------------------------------------------------------------------

    def _room(self, size: int) -> bool:
        m = self.manifest
        if m["frame_count"] >= self.cfg.max_frames or m["bytes"] + size > self.cfg.max_bytes:
            m["truncated"] = True
            return False
        return True

    def add_frame(self, jpeg: bytes, *, force: bool = False) -> int | None:
        now = time.time()
        if not force and now - self._last_frame_t < 1.0 / self.cfg.fps:
            return None
        if not jpeg or not self._room(len(jpeg)):
            return None
        self._last_frame_t = now
        m = self.manifest
        m["frame_count"] += 1
        name = f"f{m['frame_count']:06d}.jpg"
        (self.dir / name).write_bytes(jpeg)
        m["bytes"] += len(jpeg)
        m["frames"].append({"file": name, "t": round(now - m["started_at"], 3)})
        return m["frame_count"] - 1

    def start(self, page) -> None:
        """Begin screencasting ``page`` (Playwright Page). Best effort."""
        try:
            cdp = page.context.new_cdp_session(page)
        except Exception:  # noqa: BLE001
            return
        self._cdp = cdp

        def on_frame(params: dict[str, Any]) -> None:
            with contextlib.suppress(Exception):
                cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
            with contextlib.suppress(Exception):
                self.add_frame(base64.b64decode(params.get("data") or ""))

        with contextlib.suppress(Exception):
            cdp.on("Page.screencastFrame", on_frame)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.cfg.quality, "maxWidth": self.cfg.max_width, "maxHeight": 4000, "everyNthFrame": 1})

    def finish(self, page, tool: str, detail: dict[str, Any], *, ok: bool, error: str | None = None) -> None:
        frame_idx = None
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                self._cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                shot = self._cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": self.cfg.quality})
                frame_idx = self.add_frame(base64.b64decode(shot.get("data") or ""), force=True)
            with contextlib.suppress(Exception):
                self._cdp.detach()
        if frame_idx is None and self.manifest["frames"]:
            frame_idx = len(self.manifest["frames"]) - 1
        page_url = ""
        with contextlib.suppress(Exception):
            page_url = page.url
        m = self.manifest
        action = sanitize_action(tool, detail)
        action.update({"t": round(time.time() - m["started_at"], 3), "ok": ok, "frame": frame_idx, "page_url": redact_url(page_url)[:500]})
        if error:
            action["error"] = error[:300]
        m["actions"].append(action)
        m["action_count"] = len(m["actions"])
        m["updated_at"] = time.time()
        tmp = self.dir / f".manifest.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps(m), encoding="utf-8")
        os.replace(tmp, self.dir / "manifest.json")
