"""Browser-layer tools: profiles, downloads, PDF and the human hand-off.

They drive the same sandbox Chromium as ``browser_navigate`` & co. through
``workspace_tools._cdp_browser_op`` (circuit breaker, bounded connect retry,
typed errors), so they inherit its failure semantics.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from langchain.tools import tool

from nova.browser import thread_state
from nova.browser.profiles import ProfileError, ProfileStore
from nova.config.browser_config import get_browser_config
from nova.tools.builtins.workspace_tools import _aio_client_and_thread, _browser_layer, _cdp_browser_op
from nova.tools.types import Runtime

_PDF_MAX_BYTES = 50_000_000
_PDF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-/]{0,200}\.pdf$")
_PAPER = {"A4": (8.27, 11.69), "Letter": (8.5, 11.0), "Legal": (8.5, 14.0), "A3": (11.69, 16.54)}


def _user_and_dirs(runtime: Runtime):
    layer = _browser_layer(runtime, "", {})
    return layer.user_id, layer.outputs_dir, layer.thread_dir, layer.thread_id


def _default_domains(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower()
    return [host[4:] if host.startswith("www.") else host] if host else []


@tool("browser_profile_save", parse_docstring=True)
def browser_profile_save_tool(runtime: Runtime, description: str, name: str, domains: list[str] | None = None, make_default: bool = False) -> str:
    """Save the sandbox browser's current login (cookies + localStorage) as a named profile.

    The profile is encrypted at rest, private to the user, and scoped to
    ``domains`` — it is only ever sent to those sites (and their subdomains).
    Later, ``browser_profile_load`` restores it in any thread, and
    ``web_fetch`` / ``web_fetch_many`` / ``web_crawl`` send its cookies to
    matching URLs automatically.

    Args:
        description: Why you are saving this login. ALWAYS PROVIDE THIS FIRST.
        name: Profile name, e.g. "github-work" (letters, digits, '.', '_', '-').
        domains: Sites the profile covers, e.g. ["github.com"]. Defaults to the current page's site.
        make_default: Load this profile automatically in new threads.
    """
    client, tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    user_id, _out, thread_dir, _ = _user_and_dirs(runtime)
    if not user_id:
        return "Error: no user context — profiles are per user."
    try:
        captured: dict = {}

        def _grab(page):
            captured["url"] = page.url
            captured["state"] = page.context.storage_state()
            return ""

        _cdp_browser_op(client, _grab, thread_id=tid)
        doms = domains or _default_domains(captured.get("url", ""))
        meta = ProfileStore(user_id).save(name, captured.get("state") or {}, doms, make_default=make_default)
        if thread_dir:
            thread_state.update_settings(thread_dir, profile=meta["name"])
        return f"Saved browser profile {meta['name']!r} for {', '.join(meta['domains'])}: {meta['cookie_count']} cookie(s), {meta['origin_count']} localStorage origin(s), encrypted." + (
            " It is now your default profile." if meta["is_default"] else ""
        )
    except ProfileError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"


@tool("browser_profile_load", parse_docstring=True)
def browser_profile_load_tool(runtime: Runtime, description: str, name: str) -> str:
    """Load a saved browser profile into the sandbox browser so its sites are logged in.

    Args:
        description: Why you are loading this profile. ALWAYS PROVIDE THIS FIRST.
        name: The profile name (see Settings › Browser profiles, or the error for the list).
    """
    client, tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    user_id, _out, thread_dir, _ = _user_and_dirs(runtime)
    if not user_id:
        return "Error: no user context — profiles are per user."
    store = ProfileStore(user_id)
    try:
        if not store.exists(name):
            names = ", ".join(m["name"] for m in store.list()) or "none saved yet"
            return f"Error: no browser profile named {name!r} (available: {names})."
        if thread_dir:
            thread_state.update_settings(thread_dir, profile=name)
        thread_state.forget(tid)
        layer = _browser_layer(runtime, "browser_profile_load", {"name": name})
        _cdp_browser_op(client, lambda page: "", thread_id=tid, layer=layer)
        if layer.profile_loaded != name:
            return f"Error: profile {name!r} could not be applied (see server log)."
        meta = next((m for m in store.list() if m["name"] == name), {})
        return f"Loaded browser profile {name!r} ({meta.get('cookie_count', 0)} cookie(s) for {', '.join(meta.get('domains') or [])}). Navigate to the site to use it."
    except ProfileError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"


@tool("browser_downloads", parse_docstring=True)
def browser_downloads_tool(runtime: Runtime, description: str) -> str:
    """List files the sandbox browser downloaded into /mnt/user-data/outputs/downloads/.

    Args:
        description: Why you are listing downloads. ALWAYS PROVIDE THIS FIRST.
    """
    from nova.browser.downloads import list_downloads

    _uid, outputs, _td, _tid = _user_and_dirs(runtime)
    if outputs is None:
        return "Error: this thread has no outputs directory."
    rows = list_downloads(outputs)
    if not rows:
        return "No downloads yet. A browser_click that triggers a download saves the file here automatically."
    return "\n".join(f"{r['path']}  ({r['bytes']:,} bytes)" for r in rows)


def _safe_pdf_relpath(path: str) -> str:
    rel = (path or "").strip()
    if rel.startswith("/mnt/user-data/outputs/"):
        rel = rel[len("/mnt/user-data/outputs/") :]
    if not rel:
        rel = time.strftime("page-%Y%m%d-%H%M%S.pdf")
    if not rel.lower().endswith(".pdf"):
        rel += ".pdf"
    parts = PurePosixPath(rel).parts
    if rel.startswith("/") or any(p in ("..", ".") or p.startswith(".") for p in parts) or not _PDF_NAME_RE.match(rel):
        raise ValueError("path must be a relative file name under /mnt/user-data/outputs ending in .pdf (no '..' or hidden parts)")
    return rel


@tool("browser_pdf", parse_docstring=True)
def browser_pdf_tool(
    runtime: Runtime,
    description: str,
    url: str = "",
    path: str = "",
    landscape: bool = False,
    print_background: bool = True,
    scale: float = 1.0,
    paper: str = "A4",
) -> str:
    """Print a page in the sandbox browser to PDF under /mnt/user-data/outputs/.

    Args:
        description: Why you are making this PDF. ALWAYS PROVIDE THIS FIRST.
        url: Page to open first. Empty prints the page currently shown.
        path: Output file relative to /mnt/user-data/outputs (default page-<timestamp>.pdf).
        landscape: Landscape orientation.
        print_background: Include background colours and images.
        scale: Rendering scale, 0.1-2.
        paper: A4, Letter, Legal or A3.
    """
    client, tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    _uid, outputs, _td, _ = _user_and_dirs(runtime)
    if outputs is None:
        return "Error: this thread has no outputs directory."
    try:
        rel = _safe_pdf_relpath(path)
        width, height = _PAPER.get(paper, _PAPER["A4"])
        params = {"landscape": bool(landscape), "printBackground": bool(print_background), "scale": max(0.1, min(float(scale), 2.0)), "paperWidth": width, "paperHeight": height}
        layer = _browser_layer(runtime, "browser_pdf", {"url": url, "path": rel})

        def _print(page):
            if url:
                page.goto(url, wait_until="load", timeout=30000)
            cdp = page.context.new_cdp_session(page)
            try:
                data = cdp.send("Page.printToPDF", params).get("data") or ""
            finally:
                cdp.detach()
            return data

        data = _cdp_browser_op(client, _print, thread_id=tid, layer=layer if (url and layer.wants_cdp()) else None)
        pdf = base64.b64decode(data)
        if not pdf.startswith(b"%PDF"):
            return "Error: the browser did not return a PDF."
        if len(pdf) > _PDF_MAX_BYTES:
            return f"Error: PDF is {len(pdf):,} bytes, over the {_PDF_MAX_BYTES:,}-byte cap."
        target = (Path(outputs) / rel).resolve()
        if not str(target).startswith(str(Path(outputs).resolve()) + "/"):
            return "Error: path escapes the outputs directory."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pdf)
        return f"Saved PDF: /mnt/user-data/outputs/{rel} ({len(pdf):,} bytes)"
    except Exception as e:
        return f"Error: {e}"


@tool("browser_await_human", parse_docstring=True)
def browser_await_human_tool(runtime: Runtime, description: str, timeout_s: int = 300) -> str:
    """Wait for the user to finish a captcha / login in the Browser tab's live view.

    Use after a browser tool or web_fetch returned NEEDS_HUMAN. Returns when the
    user presses "Done" in the Browser tab or the challenge disappears from the
    page, or after ``timeout_s``.

    Args:
        description: What the user is being asked to do. ALWAYS PROVIDE THIS FIRST.
        timeout_s: How long to wait, 10-900 seconds (default 300).
    """
    from nova.browser.challenge import probe_page

    client, tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    _uid, _out, thread_dir, _ = _user_and_dirs(runtime)
    deadline = time.monotonic() + max(10, min(int(timeout_s), 900))
    how = "timeout"
    page_url = ""
    while time.monotonic() < deadline:
        att = thread_state.read_attention(thread_dir)
        if att and att.get("resolved_at"):
            how = "user_done"
            break
        try:
            state = _cdp_browser_op(client, lambda page: json.dumps({"url": page.url, "blocked": probe_page(page) is not None}), thread_id=tid)
            info = json.loads(state)
            page_url = info.get("url", "")
            if not info.get("blocked"):
                how = "challenge_cleared"
                break
        except Exception:
            pass
        time.sleep(4)
    if how == "timeout":
        return json.dumps({"status": "still_waiting", "url": page_url, "next": "The user has not finished yet. Ask whether they need more time, then call browser_await_human again."})
    thread_state.clear_attention(thread_dir)
    hint = "Offer to save this login with browser_profile_save so it is reused next time." if get_browser_config().profiles.enabled else ""
    return json.dumps({"status": "continue", "how": how, "url": page_url, "next": "Continue the task in the sandbox browser.", "after": hint})


def sandbox_fetcher(runtime: Runtime | None):
    """Last rung of the web_fetch ladder: an async ``url -> {html, block} | None``.

    Only offered when the thread *already* has a sandbox — a page fetch must
    never be what spins up a container. Opens the URL in a fresh tab of the
    sandbox Chromium with the user's matching profile applied; the tab is
    closed afterwards unless it shows a captcha, in which case it is left open
    in the live view and the Browser tab is asked to prompt the user.
    """
    if runtime is None or not ((getattr(runtime, "state", None) or {}).get("sandbox") or {}).get("sandbox_id"):
        return None

    def _run(url: str) -> dict:
        from nova.browser.challenge import probe_page
        from nova.browser.session import apply_profile

        client, tid, err = _aio_client_and_thread(runtime)
        if err:
            return {"error": err}
        layer = _browser_layer(runtime, "web_fetch", {"url": url})
        cfg = layer.cfg

        def _op(page):
            if layer.user_id and cfg.profiles.enabled:
                prof = ProfileStore(layer.user_id, config=cfg.profiles).match_url(url)
                if prof is not None:
                    apply_profile(page.context, page, layer.user_id, prof.name, cfg)
            keep_open = False
            try:
                page.goto(url, wait_until="load", timeout=30000)
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=5000)
                block = probe_page(page) if cfg.captcha_detection else None
                if block is not None and block.needs_human:
                    keep_open = True
                    with contextlib.suppress(Exception):
                        page.bring_to_front()
                    thread_state.raise_attention(layer.thread_dir, kind=block.kind, url=page.url, title=page.title(), reason=block.reason)
                return {"html": page.content(), "block": block}
            finally:
                if not keep_open:
                    with contextlib.suppress(Exception):
                        page.close()

        return _cdp_browser_op(client, _op, thread_id=tid, layer=layer, fresh_page=True)

    async def fetch(url: str) -> dict:
        import asyncio

        return await asyncio.to_thread(_run, url)

    return fetch


BROWSER_LAYER_TOOLS = [
    browser_profile_save_tool,
    browser_profile_load_tool,
    browser_downloads_tool,
    browser_pdf_tool,
    browser_await_human_tool,
]
