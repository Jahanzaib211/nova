"""Capture sandbox-browser downloads into the thread's outputs.

Playwright's own download handling cannot be used here: over
``connect_over_cdp`` it points Chromium at a temp directory on the *gateway's*
filesystem, which does not exist inside the sandbox, so a download started
during a Nova tool call simply failed. We take the behaviour back with
``Browser.setDownloadBehavior`` and a path the sandbox can write —
``/mnt/user-data/outputs/downloads`` — which is the same directory the gateway
sees as ``{outputs_path}/downloads``, so the file shows up in the Files tab.
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

DOWNLOADS_SUBDIR = "downloads"
VIRTUAL_OUTPUTS = "/mnt/user-data/outputs"
MAX_LISTED = 200


@dataclass
class _Item:
    guid: str
    filename: str
    url: str
    state: str = "inProgress"
    received: int = 0
    total: int = 0


@dataclass
class DownloadWatcher:
    """Listens on a browser-level CDP session for downloads started during one tool call."""

    browser_dir: str  # directory as the *browser* sees it
    items: dict[str, _Item] = field(default_factory=dict)
    _cdp: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def attach(self, browser) -> bool:
        try:
            cdp = browser.new_browser_cdp_session()
            cdp.on("Browser.downloadWillBegin", self._on_begin)
            cdp.on("Browser.downloadProgress", self._on_progress)
            cdp.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": self.browser_dir, "eventsEnabled": True})
        except Exception:  # noqa: BLE001 - downloads are best effort
            return False
        self._cdp = cdp
        return True

    def _on_begin(self, p: dict[str, Any]) -> None:
        name = PurePosixPath(str(p.get("suggestedFilename") or "download")).name or "download"
        with self._lock:
            self.items[str(p.get("guid"))] = _Item(guid=str(p.get("guid")), filename=name, url=str(p.get("url") or ""))

    def _on_progress(self, p: dict[str, Any]) -> None:
        with self._lock:
            item = self.items.get(str(p.get("guid")))
            if item is None:
                return
            item.state = str(p.get("state") or item.state)
            item.received = int(p.get("receivedBytes") or 0)
            item.total = int(p.get("totalBytes") or 0)
            fp = p.get("filePath")
            if fp:
                item.filename = PurePosixPath(str(fp)).name or item.filename

    def wait(self, page, *, begin_s: float = 1.5, finish_s: float = 20.0) -> list[dict[str, Any]]:
        """Pump events until downloads that began during the op finish (bounded)."""
        deadline = time.monotonic() + begin_s
        while not self.items and time.monotonic() < deadline:
            _pump(page, 100)
        if not self.items:
            return []
        deadline = time.monotonic() + finish_s
        while time.monotonic() < deadline:
            with self._lock:
                if all(i.state != "inProgress" for i in self.items.values()):
                    break
            _pump(page, 200)
        with self._lock:
            return [{"filename": i.filename, "state": i.state, "bytes": i.received, "path": f"{self.browser_dir.rstrip('/')}/{i.filename}"} for i in self.items.values()]

    def detach(self) -> None:
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                self._cdp.detach()


def _pump(page, ms: int) -> None:
    # Playwright's sync API only dispatches events while it is inside a call.
    try:
        page.wait_for_timeout(ms)
    except Exception:  # noqa: BLE001 - page closed by the download itself
        time.sleep(ms / 1000)


def list_downloads(outputs_host_dir: Path) -> list[dict[str, Any]]:
    root = Path(outputs_host_dir) / DOWNLOADS_SUBDIR
    if not root.is_dir():
        return []
    rows = []
    for p in root.iterdir():
        if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".crdownload"):
            st = p.stat()
            rows.append({"name": p.name, "path": f"{VIRTUAL_OUTPUTS}/{DOWNLOADS_SUBDIR}/{p.name}", "bytes": st.st_size, "modified": st.st_mtime})
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return rows[:MAX_LISTED]
