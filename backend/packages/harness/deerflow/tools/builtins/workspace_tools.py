"""Workspace search tools — search_files and grep_files.

These tools let agents locate files by name pattern (search_files)
or search file contents by regex/string (grep_files).  Both operate
inside the agent's sandbox workspace at /mnt/user-data/workspace/.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import os
import re
import shlex
import threading
from pathlib import Path

from langchain.tools import tool

from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.sandbox.tools import (
    _extract_thread_id_from_thread_data,
    _write_sandbox_observation,
    ensure_sandbox_initialized,
    get_thread_data,
    is_local_sandbox,
)
from deerflow.tools.types import Runtime

_VIRTUAL_WORKSPACE = "/mnt/user-data/workspace"
_MAX_SEARCH_RESULTS = 100
_MAX_GREP_RESULTS = 200
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", ".cache", "dist", ".next"}


def _resolve_workspace_host_path(runtime: Runtime) -> Path | None:
    """Resolve /mnt/user-data/workspace to the host filesystem path for local sandboxes."""
    if not is_local_sandbox(runtime):
        return None
    try:
        thread_data = get_thread_data(runtime)
        workspace_path = thread_data.get("workspace_path")
        if workspace_path:
            return Path(workspace_path)
    except Exception:
        pass
    return None


def _get_sandbox_id(runtime: Runtime) -> str:
    if hasattr(runtime, "state"):
        return (runtime.state.get("sandbox") or {}).get("sandbox_id", "")
    return ""


@tool("search_files", parse_docstring=True)
def search_files_tool(
    runtime: Runtime,
    description: str,
    pattern: str,
    directory: str = _VIRTUAL_WORKSPACE,
) -> str:
    """Find files matching a filename glob pattern in the sandbox workspace.

    Args:
        description: Explain why you are searching. ALWAYS PROVIDE THIS PARAMETER FIRST.
        pattern: Glob pattern to match filenames, e.g. "*.html", "style*.css", "*.py".
        directory: Directory to search in. Defaults to /mnt/user-data/workspace.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        sandbox_id = _get_sandbox_id(runtime)

        if is_local_sandbox(runtime):
            host_dir = _resolve_workspace_host_path(runtime)
            if host_dir is None or not host_dir.exists():
                return f"Error: Workspace not found at {directory}"

            matches: list[str] = []
            for root, dirs, files in os.walk(host_dir):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
                for fname in files:
                    if fnmatch.fnmatch(fname, pattern):
                        full = Path(root) / fname
                        rel = full.relative_to(host_dir)
                        matches.append(f"/mnt/user-data/workspace/{rel.as_posix()}")
                        if len(matches) >= _MAX_SEARCH_RESULTS:
                            break
                if len(matches) >= _MAX_SEARCH_RESULTS:
                    break

            result = "\n".join(matches) if matches else f"No files matching '{pattern}' found."
            _write_sandbox_observation(
                sandbox_id,
                "search_files",
                directory,
                f"Pattern '{pattern}' → {len(matches)} match(es)",
                result,
            )
            return result
        else:
            # AIO / remote sandbox — use bash; quote all shell-interpolated values
            cmd = f"find {shlex.quote(directory)} -name {shlex.quote(pattern)} -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null | head -{_MAX_SEARCH_RESULTS}"
            result = sandbox.execute_command(cmd)
            _write_sandbox_observation(
                sandbox_id,
                "search_files",
                directory,
                f"Pattern '{pattern}'",
                result,
            )
            return result or f"No files matching '{pattern}' found."
    except Exception as e:
        return f"Error: {e}"


@tool("grep_files", parse_docstring=True)
def grep_files_tool(
    runtime: Runtime,
    description: str,
    pattern: str,
    directory: str = _VIRTUAL_WORKSPACE,
    file_pattern: str = "*",
) -> str:
    """Search file contents for a string or regex pattern in the sandbox workspace.

    Args:
        description: Explain why you are searching. ALWAYS PROVIDE THIS PARAMETER FIRST.
        pattern: String or regex to search for in file contents.
        directory: Directory to search in. Defaults to /mnt/user-data/workspace.
        file_pattern: Glob pattern to filter which files to search, e.g. "*.html". Defaults to "*" (all files).
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        sandbox_id = _get_sandbox_id(runtime)

        if is_local_sandbox(runtime):
            host_dir = _resolve_workspace_host_path(runtime)
            if host_dir is None or not host_dir.exists():
                return f"Error: Workspace not found at {directory}"

            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                return f"Error: Invalid regex pattern '{pattern}': {e}"

            matches: list[str] = []
            for root, dirs, files in os.walk(host_dir):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
                for fname in files:
                    if not fnmatch.fnmatch(fname, file_pattern):
                        continue
                    fpath = Path(root) / fname
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="ignore")
                        for lineno, line in enumerate(text.splitlines(), 1):
                            if regex.search(line):
                                rel = fpath.relative_to(host_dir)
                                vpath = f"/mnt/user-data/workspace/{rel.as_posix()}"
                                matches.append(f"{vpath}:{lineno}: {line.strip()}")
                                if len(matches) >= _MAX_GREP_RESULTS:
                                    break
                    except OSError:
                        continue
                    if len(matches) >= _MAX_GREP_RESULTS:
                        break
                if len(matches) >= _MAX_GREP_RESULTS:
                    break

            result = "\n".join(matches) if matches else f"No matches for '{pattern}'."
            _write_sandbox_observation(
                sandbox_id,
                "grep_files",
                directory,
                f"'{pattern}' in {file_pattern} → {len(matches)} match(es)",
                result[:1000],
            )
            return result
        else:
            # AIO / remote sandbox — use bash grep; quote all shell-interpolated values
            cmd = f"grep -rn {shlex.quote(pattern)} {shlex.quote(directory)} --include={shlex.quote(file_pattern)} --exclude-dir=node_modules --exclude-dir=.git 2>/dev/null | head -{_MAX_GREP_RESULTS}"
            result = sandbox.execute_command(cmd)
            _write_sandbox_observation(
                sandbox_id,
                "grep_files",
                directory,
                f"'{pattern}'",
                result,
            )
            return result or f"No matches for '{pattern}'."
    except Exception as e:
        return f"Error: {e}"


# ── scaffold_project ──────────────────────────────────────

_TEMPLATES: dict[str, dict[str, str]] = {
    "nextjs-tailwind": {
        "package.json": '{"name":"my-app","version":"0.1.0","private":true,"scripts":{"dev":"next dev -H 0.0.0.0","build":"next build","start":"next start -H 0.0.0.0"},"dependencies":{"next":"14.2.0","react":"^18","react-dom":"^18"},"devDependencies":{"typescript":"^5","tailwindcss":"^3.4.0","autoprefixer":"^10.4.0","postcss":"^8.4.0","@types/node":"^20","@types/react":"^18","@types/react-dom":"^18"}}',
        "tsconfig.json": '{"compilerOptions":{"target":"es5","lib":["dom","dom.iterable","esnext"],"allowJs":true,"skipLibCheck":true,"strict":true,"noEmit":true,"esModuleInterop":true,"module":"esnext","moduleResolution":"bundler","resolveJsonModule":true,"isolatedModules":true,"jsx":"preserve","incremental":true,"paths":{"@/*":["./src/*"]}},"include":["next-env.d.ts","**/*.ts","**/*.tsx"],"exclude":["node_modules"]}',
        "tailwind.config.ts": "import type { Config } from 'tailwindcss'\nconst config: Config = {\n  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],\n  theme: { extend: {} },\n  plugins: [],\n}\nexport default config",
        "postcss.config.js": "module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } }",
        "next.config.ts": "import type { NextConfig } from 'next'\nconst nextConfig: NextConfig = {}\nexport default nextConfig",
        "src/app/globals.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;",
        "src/app/layout.tsx": "import type { Metadata } from 'next'\nimport './globals.css'\nexport const metadata: Metadata = { title: 'My App', description: 'Built by DeerFlow' }\nexport default function RootLayout({ children }: { children: React.ReactNode }) {\n  return (<html lang=\"en\"><body>{children}</body></html>)\n}",
        "src/app/page.tsx": 'export default function Home() {\n  return (<main className="min-h-screen p-8"><h1 className="text-4xl font-bold">My App</h1></main>)\n}',
    },
    "react-vite": {
        "package.json": '{"name":"my-app","private":true,"version":"0.0.0","type":"module","scripts":{"dev":"vite --host 0.0.0.0","build":"tsc && vite build"},"dependencies":{"react":"^18.3.1","react-dom":"^18.3.1"},"devDependencies":{"@vitejs/plugin-react":"^4.3.1","typescript":"^5.5.3","vite":"^5.4.0","tailwindcss":"^3.4.0"}}',
        "vite.config.ts": "import { defineConfig } from 'vite'\nimport react from '@vitejs/plugin-react'\nexport default defineConfig({ plugins: [react()], server: { host: true } })",
        "index.html": '<!doctype html>\n<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/><title>My App</title></head><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>',
        "src/main.tsx": "import { StrictMode } from 'react'\nimport { createRoot } from 'react-dom/client'\nimport './index.css'\nimport App from './App.tsx'\ncreateRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)",
        "src/App.tsx": 'import \'./App.css\'\nexport default function App() {\n  return <div className="min-h-screen p-8"><h1 className="text-4xl font-bold">My App</h1></div>\n}',
        "src/index.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;",
    },
    "html-only": {
        "index.html": '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8"/>\n  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>\n  <title>My Page</title>\n  <style>* { margin:0; padding:0; box-sizing:border-box; } body { font-family: system-ui, sans-serif; }</style>\n</head>\n<body>\n</body>\n</html>',
    },
}


@tool("scaffold_project", parse_docstring=True)
def scaffold_project_tool(
    runtime: Runtime,
    description: str,
    template: str = "nextjs-tailwind",
) -> str:
    """Scaffold a project template in /mnt/user-data/workspace/.

    Creates the initial file structure (package.json, tsconfig, src/, etc.)
    so you can immediately start writing component files with write_file.
    Use this FIRST before writing any project files.

    Args:
        description: Why you are scaffolding. ALWAYS PROVIDE THIS FIRST.
        template: Project template. Options: "nextjs-tailwind" (default), "react-vite", "html-only".
    """
    try:
        sandbox_id = _get_sandbox_id(runtime)
        files = _TEMPLATES.get(template) or _TEMPLATES["html-only"]
        created: list[str] = []

        if is_local_sandbox(runtime):
            host_dir = _resolve_workspace_host_path(runtime)
            if host_dir is None:
                return "Error: Workspace not found"
            host_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in files.items():
                dest = host_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                created.append(f"/mnt/user-data/workspace/{rel_path}")
        else:
            sandbox = ensure_sandbox_initialized(runtime)
            import base64

            for rel_path, content in files.items():
                vpath = f"/mnt/user-data/workspace/{rel_path}"
                parent = "/".join(vpath.split("/")[:-1])
                sandbox.execute_command(f"mkdir -p {shlex.quote(parent)}")
                b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
                sandbox.execute_command(f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(vpath)}")
                created.append(vpath)

        summary = f"Scaffolded '{template}': {len(created)} files"
        _write_sandbox_observation(sandbox_id, "scaffold_project", _VIRTUAL_WORKSPACE, summary)
        return f"✓ {summary}\n" + "\n".join(f"  {p}" for p in created)
    except Exception as e:
        return f"Error: {e}"


# ── start_dev_server / stop_dev_server ─────────────────────


def _thread_id_from_sandbox_id(sandbox_id: str) -> str | None:
    if sandbox_id and sandbox_id.startswith("local:"):
        tid = sandbox_id[len("local:") :]
        return tid or None
    return None


@tool("start_dev_server", parse_docstring=True)
async def start_dev_server_tool(
    runtime: Runtime,
    description: str,
    command: str = "npm run dev",
    subdir: str = "",
    label: str = "app",
) -> str:
    """Start a live dev server so the user can preview the running app in the Browser tab.

    Use this AFTER `npm install` succeeds, for runnable web projects (Next.js, Vite, etc.).
    The server runs in the background and the Browser tab will auto-load the live app.
    For a single static page, write a self-contained index.html instead of using this.

    Args:
        description: Explain why you are starting the server. ALWAYS PROVIDE THIS FIRST.
        command: The dev command to run (default "npm run dev").
        subdir: Optional project subdirectory under the workspace (e.g. "my-app").
        label: Name for this server when running MORE THAN ONE at once (e.g. "app", "api").
            Leave as "app" for a single server. Each label gets its own preview in the Browser tab.
    """
    try:
        from deerflow.sandbox.dev_server import allocate_container_port
        from deerflow.sandbox.dev_server import start_dev_server as _start

        sandbox_id = _get_sandbox_id(runtime)
        label = label or "app"

        if is_local_sandbox(runtime):
            thread_id = _thread_id_from_sandbox_id(sandbox_id)
            if not thread_id:
                return "Error: live dev server requires a per-thread sandbox."
            host_dir = _resolve_workspace_host_path(runtime)
            if host_dir is None:
                return "Error: Workspace not found."
            cwd = str(host_dir / subdir) if subdir else str(host_dir)
            handle = await _start(thread_id, cwd, command, label=label)
        else:
            # Container sandbox (AIO): run inside the container at the virtual path.
            thread_id = _extract_thread_id_from_thread_data(get_thread_data(runtime))
            if not thread_id or not sandbox_id:
                return "Error: live dev server requires a per-thread sandbox."
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is None:
                return "Error: sandbox not available."
            cwd = f"{_VIRTUAL_WORKSPACE}/{subdir}" if subdir else _VIRTUAL_WORKSPACE
            container_port = allocate_container_port(thread_id, label)
            handle = await _start(thread_id, cwd, command, sandbox=sandbox, label=label, container_port=container_port)

        _write_sandbox_observation(
            sandbox_id,
            "start_dev_server",
            None,
            f"Dev server preview {handle.host}:{handle.port} ({handle.status})",
        )
        if handle.status == "error":
            recent = "\n".join(list(handle.log_buffer)[-10:])
            return f"Error starting dev server:\n{recent}"
        return f"✓ Dev server starting. The Browser tab in Agent's Computer will show the live app once it's ready (preview URL: /api/sandbox/preview/{thread_id}/). It may take 10-30s to compile."
    except Exception as e:
        return f"Error: {e}"


# ── Enterprise nodes: interactive PTY · browser automation · deploy · notify ──
# All wrap the AIO sandbox's native SDK (client.shell / client.browser_page /
# client.proxy) that DeerFlow otherwise under-uses. Container (AIO) sandbox only.


def _aio_client_and_thread(runtime: Runtime):
    """Resolve (client, thread_id, error). client is the agent_sandbox SDK client."""
    if is_local_sandbox(runtime):
        return None, None, "Error: this tool requires the container (AIO) sandbox."
    sandbox_id = _get_sandbox_id(runtime)
    try:
        thread_id = _extract_thread_id_from_thread_data(get_thread_data(runtime))
    except Exception:
        thread_id = None
    if not thread_id or not sandbox_id:
        return None, None, "Error: this tool requires a per-thread sandbox."
    sandbox = get_sandbox_provider().get(sandbox_id)
    if sandbox is None:
        return None, None, "Error: sandbox not available."
    client = getattr(sandbox, "_client", None)
    if client is None:
        return None, None, "Error: sandbox client unavailable."
    return client, thread_id, None


# Idempotency cache for browser_navigate. Maps (thread_id, navigate_id) →
# cached result, with TTL eviction. Bounded by entry count to prevent
# memory growth on long-running threads.
_NAVIGATE_IDEMPOTENCY_MAX_ENTRIES = int(os.environ.get("DEERFLOW_BROWSER_NAVIGATE_IDEMPOTENCY_MAX_ENTRIES", "256"))
_NAVIGATE_IDEMPOTENCY_TTL_S = float(os.environ.get("DEERFLOW_BROWSER_NAVIGATE_IDEMPOTENCY_TTL_S", "60.0"))


class _BrowserNavigateIdempotency:
    """Thread-safe LRU+TTL cache for browser_navigate results.

    Storage layout: dict[(thread_id, navigate_id), (timestamp, result)].
    Eviction: opportunistic on put() — if size exceeds the cap, drop
    the oldest entry by timestamp.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, thread_id: str, navigate_id: str) -> str | None:
        import time as _time

        now = _time.monotonic()
        with self._lock:
            entry = self._data.get((thread_id, navigate_id))
            if entry is None:
                return None
            ts, result = entry
            if (now - ts) > _NAVIGATE_IDEMPOTENCY_TTL_S:
                del self._data[(thread_id, navigate_id)]
                return None
            return result

    def put(self, thread_id: str, navigate_id: str, result: str) -> None:
        import time as _time

        now = _time.monotonic()
        with self._lock:
            self._data[(thread_id, navigate_id)] = (now, result)
            # Opportunistic eviction if over the cap.
            if len(self._data) > _NAVIGATE_IDEMPOTENCY_MAX_ENTRIES:
                oldest_key = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest_key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_browser_navigate_idempotency = _BrowserNavigateIdempotency()


def _data_str(resp) -> str:
    """Best-effort stringify of a Fern response's .data."""
    data = getattr(resp, "data", resp)
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for attr in ("output", "content", "text", "result", "url"):
        v = getattr(data, attr, None)
        if v is not None:
            return str(v)
    return str(data)


@tool("shell_session", parse_docstring=True)
def shell_session_tool(
    runtime: Runtime,
    description: str,
    command: str,
    session_id: str = "main",
    exec_dir: str = _VIRTUAL_WORKSPACE,
) -> str:
    """Run a command in a PERSISTENT, possibly interactive terminal session.

    Unlike `bash` (one-shot), this keeps a named PTY session alive so you can run
    long-running or interactive programs (REPLs, watchers, prompts) and then drive
    them with `shell_view`, `shell_write`, `shell_wait`, and `shell_kill`.

    Args:
        description: Why you are running this. ALWAYS PROVIDE THIS FIRST.
        command: The command to run in the session.
        session_id: Name of the session (default "main"). Reuse to run in the same shell.
        exec_dir: Working directory (default /mnt/user-data/workspace).
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        try:
            client.shell.create_session(id=session_id, exec_dir=exec_dir)
        except Exception:
            pass  # session may already exist
        resp = client.shell.exec_command(command=command, id=session_id, exec_dir=exec_dir, async_mode=True)
        _write_sandbox_observation(_get_sandbox_id(runtime), "shell_session", exec_dir, f"[{session_id}] {command[:80]}", _data_str(resp)[:500])
        return f"Started in session '{session_id}'. Use shell_view(session_id='{session_id}') to see output.\n{_data_str(resp)[:1500]}"
    except Exception as e:
        return f"Error: {e}"


@tool("shell_view", parse_docstring=True)
def shell_view_tool(runtime: Runtime, description: str, session_id: str = "main") -> str:
    """View the current output of a terminal session.

    Args:
        description: Why you are viewing. ALWAYS PROVIDE THIS FIRST.
        session_id: The session to view (default "main").
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        return _data_str(client.shell.view(id=session_id))[:4000] or "(no output)"
    except Exception as e:
        return f"Error: {e}"


@tool("shell_wait", parse_docstring=True)
def shell_wait_tool(runtime: Runtime, description: str, session_id: str = "main", seconds: int = 30) -> str:
    """Wait for the process in a terminal session to finish (or timeout).

    Args:
        description: Why you are waiting. ALWAYS PROVIDE THIS FIRST.
        session_id: The session (default "main").
        seconds: Max seconds to wait (default 30).
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        return _data_str(client.shell.wait_for_process(id=session_id, seconds=seconds))[:4000] or "(done)"
    except Exception as e:
        return f"Error: {e}"


@tool("shell_write", parse_docstring=True)
def shell_write_tool(runtime: Runtime, description: str, input: str, session_id: str = "main", press_enter: bool = True) -> str:
    """Send input to an interactive process in a terminal session (answer a prompt, type into a REPL).

    Args:
        description: Why you are sending input. ALWAYS PROVIDE THIS FIRST.
        input: The text to send.
        session_id: The session (default "main").
        press_enter: Press Enter after the input (default true).
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        client.shell.write_to_process(id=session_id, input=input, press_enter=press_enter)
        return f"Sent to '{session_id}'. Use shell_view to see the result."
    except Exception as e:
        return f"Error: {e}"


@tool("shell_kill", parse_docstring=True)
def shell_kill_tool(runtime: Runtime, description: str, session_id: str = "main") -> str:
    """Kill the running process in a terminal session.

    Args:
        description: Why you are killing it. ALWAYS PROVIDE THIS FIRST.
        session_id: The session (default "main").
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        client.shell.kill_process(id=session_id)
        return f"Killed process in session '{session_id}'."
    except Exception as e:
        return f"Error: {e}"


@tool("browser_navigate", parse_docstring=True)
def browser_navigate_tool(
    runtime: Runtime,
    description: str,
    url: str,
    navigate_id: str | None = None,
) -> str:
    """Open a URL in the sandbox's real browser (watch it live in the Browser tab's VNC view).

    Args:
        description: Why you are navigating. ALWAYS PROVIDE THIS FIRST.
        url: The URL to open.
        navigate_id: Optional idempotency key. If the same (thread_id, navigate_id)
            pair was seen within ``DEERFLOW_BROWSER_NAVIGATE_IDEMPOTENCY_TTL_S``
            (default 60s), the cached result is returned without a second
            navigate call. Useful for retry-safety when the LLM re-issues the
            same instruction after a transient error.
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err

    # Idempotency short-circuit: same (thread, navigate_id) within TTL → cache hit.
    if navigate_id:
        cached = _browser_navigate_idempotency.get(_tid, navigate_id)
        if cached is not None:
            return cached

    try:
        client.browser_page.navigate(url=url, wait_until="load", timeout=20000)
        text = _data_str(client.browser_page.get_text())[:1500]
        _write_sandbox_observation(_get_sandbox_id(runtime), "browser", url, f"navigated to {url[:80]}")
        result = f"Opened {url}\n\n{text}"
        if navigate_id and _tid is not None:
            _browser_navigate_idempotency.put(_tid, navigate_id, result)
        return result
    except Exception as e:
        # Don't cache errors — let the next call (if it succeeds) populate
        # the cache, OR let the user retry with a different navigate_id.
        return f"Error: {e}"


@tool("browser_click", parse_docstring=True)
def browser_click_tool(runtime: Runtime, description: str, selector: str) -> str:
    """Click an element in the sandbox browser by CSS selector.

    Args:
        description: Why you are clicking. ALWAYS PROVIDE THIS FIRST.
        selector: CSS selector of the element to click.
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        client.browser_page.click(selector=selector)
        return f"Clicked {selector}."
    except Exception as e:
        return f"Error: {e}"


@tool("browser_input", parse_docstring=True)
def browser_input_tool(runtime: Runtime, description: str, selector: str, text: str, press_enter: bool = False) -> str:
    """Type text into an element in the sandbox browser.

    Args:
        description: Why you are typing. ALWAYS PROVIDE THIS FIRST.
        selector: CSS selector of the input.
        text: The text to type.
        press_enter: Press Enter after typing (default false).
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        client.browser_page.fill(selector=selector, value=text)
        if press_enter:
            client.browser_page.press_key(key="Enter")
        return f"Typed into {selector}."
    except Exception as e:
        return f"Error: {e}"


@tool("browser_eval", parse_docstring=True)
def browser_eval_tool(runtime: Runtime, description: str, script: str) -> str:
    """Run JavaScript in the sandbox browser and return the result.

    Args:
        description: Why you are evaluating. ALWAYS PROVIDE THIS FIRST.
        script: JavaScript expression to evaluate.
    """
    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        return _data_str(client.browser_page.evaluate(script=script))[:2000] or "(no result)"
    except Exception as e:
        return f"Error: {e}"


@tool("screenshot", parse_docstring=True)
def screenshot_tool(
    runtime: Runtime,
    description: str,
    full_page: bool = False,
    max_bytes: int = 1_500_000,
) -> str:
    """Capture the current browser viewport as a PNG and return it inline.

    Use this to self-observe mid-task: confirm a click landed, inspect
    error overlays, or grab a visual before/after for the activity feed.

    The returned ``data:image/png;base64,...`` payload is rendered by the
    frontend's MessageList just like ``browser_check`` screenshots — no
    extra UI plumbing required.

    Args:
        description: Why you are taking this screenshot. ALWAYS PROVIDE THIS FIRST.
        full_page: If True, capture the entire scrollable page; else viewport only.
        max_bytes: Reject the screenshot if the encoded PNG exceeds this size
            (default 1.5MB) so a runaway full-page capture can't blow the
            agent context window. Returns an error string in that case.
    """
    # Metric: count the attempt before we do anything.
    from deerflow.sandbox.metrics import screenshot_total

    client, _tid, err = _aio_client_and_thread(runtime)
    if err:
        screenshot_total.inc("unavailable")
        return err
    try:
        # Reuse the same CDP path as browser_check when available; fall back
        # to the SDK HTTP API. We don't go through the circuit breaker here —
        # a screenshot is observational, not a tool the agent relies on for
        # control flow, so transient failures are best surfaced as errors.
        from deerflow.sandbox.browser_check import (
            _cdp_url_for_gateway,
        )

        cdp = _cdp_url_for_gateway(client)
        png: bytes | None = None
        if cdp:
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(cdp, timeout=10000)
                    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = ctx.new_page()
                    try:
                        png = page.screenshot(full_page=full_page)
                    finally:
                        with contextlib.suppress(Exception):
                            page.close()
            except Exception:
                png = None

        if png is None:
            # Fallback: SDK HTTP API. Returns an iterator of chunks.
            page_obj = getattr(client, "browser_page", None)
            if page_obj is not None and hasattr(page_obj, "screenshot"):
                chunks = b"".join(page_obj.screenshot(full_page=full_page))
                if chunks:
                    png = chunks

        if png is None:
            screenshot_total.inc("error")
            return "Error: screenshot unavailable (no CDP connection and no SDK browser_page.screenshot)"

        if len(png) > max_bytes:
            screenshot_total.inc("too_large")
            return f"Error: screenshot too large ({len(png):,} bytes > {max_bytes:,}); retry with full_page=false or a smaller viewport"

        b64 = base64.b64encode(png).decode("ascii")
        screenshot_total.inc("ok")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        screenshot_total.inc("error")
        return f"Error: {e}"


@tool("deploy_expose", parse_docstring=True)
async def deploy_expose_tool(runtime: Runtime, description: str, port: int) -> str:
    """Expose a port running in the sandbox at a shareable preview URL.

    Returns a URL (routed through the DeerFlow gateway via the sandbox's absproxy)
    that opens the app on `port` regardless of how it was started. Use after your
    server is listening to give the user a live link.

    Args:
        description: Why you are exposing. ALWAYS PROVIDE THIS FIRST.
        port: The in-container port the app listens on.
    """
    client, thread_id, err = _aio_client_and_thread(runtime)
    if err:
        return err
    try:
        url = f"/api/sandbox/absproxy/{thread_id}/{int(port)}/"
        _write_sandbox_observation(_get_sandbox_id(runtime), "deploy_expose", None, f"exposed port {port}")
        return f"✓ Exposed port {port}. Shareable preview URL (open in the Browser tab or a new tab): {url}"
    except Exception as e:
        return f"Error: {e}"


@tool("agent_notify", parse_docstring=True)
def agent_notify_tool(runtime: Runtime, description: str, message: str) -> str:
    """Post a status note to the user's Activity feed without interrupting them.

    Use for progress milestones ("installed deps", "tests passing", "deploying").
    For questions that need an answer, use ask_clarification instead.

    Args:
        description: Why you are notifying. ALWAYS PROVIDE THIS FIRST.
        message: The short status message.
    """
    try:
        _write_sandbox_observation(_get_sandbox_id(runtime), "notify", None, message[:200])
        return "✓ Notified."
    except Exception as e:
        return f"Error: {e}"


@tool("system_probe", parse_docstring=True)
def system_probe_tool(runtime: Runtime, description: str) -> str:
    """Get a complete, expert snapshot of the sandbox computer in ONE call.

    Runs a curated battery (OS, CPU/mem/disk, installed runtimes + versions,
    listening ports, cwd, workspace contents) deterministically — the same correct
    checks every time. Use this FIRST when you need to understand the environment
    before installing, building, or starting a server (so you don't guess).

    Args:
        description: Why you are probing. ALWAYS PROVIDE THIS FIRST.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        cmd = (
            "echo '== OS =='; uname -srm; (grep -h PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"'); "
            "echo; echo '== RESOURCES =='; echo \"cpus=$(nproc 2>/dev/null)\"; "
            "free -h 2>/dev/null | awk 'NR<=2{print}'; df -h / 2>/dev/null | tail -1; "
            "echo; echo '== RUNTIMES =='; "
            "for b in node npm pnpm python3 pip uv git go rustc docker; do "
            'printf \'%-8s \' "$b"; (command -v "$b" >/dev/null 2>&1 && "$b" --version 2>&1 | head -1) || echo \'absent\'; done; '
            "echo; echo '== LISTENING PORTS =='; (ss -ltnp 2>/dev/null || netstat -ltn 2>/dev/null) | grep -i listen | head -20; "
            "echo; echo '== CWD / WORKSPACE =='; pwd; ls -la /mnt/user-data/workspace 2>/dev/null | head -25"
        )
        out = sandbox.execute_command(cmd)
        _write_sandbox_observation(_get_sandbox_id(runtime), "bash", None, "system_probe")
        return out or "(no output)"
    except Exception as e:
        return f"Error: {e}"


@tool("free_port", parse_docstring=True)
def free_port_tool(runtime: Runtime, description: str, port: int) -> str:
    """Deterministically free a TCP port — kill whatever is holding it.

    Runs the correct kill sequence (fuser, then lsof fallback) and verifies the
    port is released. Use before starting a server when a port may be taken, or to
    clean up a stray/zombie server (e.g. a forgotten http.server).

    Args:
        description: Why you are freeing the port. ALWAYS PROVIDE THIS FIRST.
        port: The TCP port to free.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        p = int(port)
        cmd = f"fuser -k {p}/tcp 2>/dev/null; kill $(lsof -ti tcp:{p} 2>/dev/null) 2>/dev/null; sleep 0.4; if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ':{p} '; then echo 'STILL IN USE: {p}'; else echo 'FREE: {p}'; fi"
        out = sandbox.execute_command(cmd)
        _write_sandbox_observation(_get_sandbox_id(runtime), "bash", None, f"free_port {p}")
        return out or f"FREE: {p}"
    except Exception as e:
        return f"Error: {e}"


@tool("dev_verify", parse_docstring=True)
def dev_verify_tool(runtime: Runtime, description: str, run_tests: bool = True) -> str:
    """Run the full senior-engineer verification battery in ONE deterministic call.

    Always runs the SAME correct sequence so verification is reliable, not guessed:
      1. Tests — detects the project's test script and runs it (bounded).
      2. Browser — `browser_check` (loads the running app or the present_files HTML,
         capturing render errors + console).
      3. Review — deterministic `code_review` (changed files, risk flags, checks).
    Call this before telling the user the work is done. Returns a consolidated verdict.

    Args:
        description: Why you are verifying. ALWAYS PROVIDE THIS FIRST.
        run_tests: Run the detected test suite (default true). Set false to skip slow tests.
    """
    try:
        from deerflow.runtime.user_context import get_effective_user_id
        from deerflow.sandbox.review import build_review

        sandbox = ensure_sandbox_initialized(runtime)
        sandbox_id = _get_sandbox_id(runtime)
        thread_id = _thread_id_from_sandbox_id(sandbox_id) or _extract_thread_id_from_thread_data(get_thread_data(runtime))
        if not thread_id:
            return "Error: dev_verify requires a per-thread sandbox."
        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None

        lines: list[str] = ["# dev_verify — senior verification battery", ""]
        ok = True

        # 1) Tests (deterministic detect → run, bounded).
        if run_tests:
            pkg = (sandbox.execute_command("find /mnt/user-data/workspace -maxdepth 2 -name package.json -not -path '*/node_modules/*' 2>/dev/null | head -1") or "").strip().splitlines()
            pkg_path = next((p.strip() for p in pkg if p.strip().endswith("package.json")), "")
            if pkg_path:
                projdir = pkg_path.rsplit("/", 1)[0]
                has_test = (sandbox.execute_command(f"grep -q '\"test\"[[:space:]]*:' {shlex.quote(pkg_path)} && echo yes || echo no") or "").strip()
                if "yes" in has_test:
                    out = sandbox.execute_command(f"cd {shlex.quote(projdir)} && timeout 180 npm test 2>&1 | tail -25; echo EXIT:${{PIPESTATUS[0]}}") or ""
                    passed = "EXIT:0" in out
                    ok = ok and passed
                    lines.append(f"## Tests: {'✓ pass' if passed else '✗ fail'}")
                    lines.append("```\n" + out[-1200:].strip() + "\n```")
                else:
                    lines.append("## Tests: (no test script defined)")
            else:
                lines.append("## Tests: (no Node project found)")
            lines.append("")

        # 2) Browser self-test (running app or present_files HTML).
        try:
            from deerflow.sandbox.browser_check import run_browser_check

            if not is_local_sandbox(runtime):
                # Capture a screenshot so a vision-capable model can SEE the build
                # (ViewImageMiddleware injects it next turn). Best-effort, non-fatal.
                chk = run_browser_check(thread_id, sandbox, with_screenshot=True)
                ok = ok and (chk.ok or not chk.routes)
                lines.append(f"## Browser: {'✓ ok' if chk.ok else '✗ ' + chk.reason}")
                for r in chk.routes:
                    lines.append(f"- {r.route} [{r.status}]" + (f" — {r.notes}" if r.notes else ""))
                lines.append("")
                try:
                    from deerflow.agents.middlewares.verify_vision import _first_route_screenshot, stash_verify_screenshot

                    stash_verify_screenshot(thread_id, _first_route_screenshot(chk))
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"## Browser: (skipped: {str(e)[:80]})\n")

        # 3) Deterministic code review.
        review = build_review(thread_id, user_id)
        high = [r for r in review.risks if r.level == "high"]
        ok = ok and not high
        lines.append(f"## Review: {len(review.files)} file(s), {len(review.risks)} risk(s)" + (f" — {len(high)} HIGH" if high else ""))
        for r in high:
            lines.append(f"- 🔴 {r.message}")

        lines.insert(1, f"**Verdict: {'✅ PASS' if ok else '⚠️ ISSUES — fix before shipping'}**")
        _write_sandbox_observation(sandbox_id, "dev_verify", None, f"verify: {'pass' if ok else 'issues'}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@tool("code_review", parse_docstring=True)
def code_review_tool(
    runtime: Runtime,
    description: str,
) -> str:
    """Run a deterministic code review of the current workspace and return the report.

    Produces a dual-audience review (plain-English verdict + per-file +/- stats,
    risk flags, and detected checks) from git/file-scan + the audit trail, and
    writes REVIEW.md into the workspace. Use this after finishing a build, or when
    the user asks "review my changes". It is deterministic (no extra model call).

    Args:
        description: Why you are reviewing. ALWAYS PROVIDE THIS FIRST.
    """
    try:
        from deerflow.runtime.user_context import get_effective_user_id
        from deerflow.sandbox.review import build_review

        sandbox_id = _get_sandbox_id(runtime)
        thread_id = _thread_id_from_sandbox_id(sandbox_id)
        if not thread_id:
            thread_id = _extract_thread_id_from_thread_data(get_thread_data(runtime))
        if not thread_id:
            return "Error: code review requires a per-thread sandbox."
        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None
        review = build_review(thread_id, user_id)
        _write_sandbox_observation(
            sandbox_id,
            "code_review",
            None,
            f"Review: {len(review.files)} file(s), {len(review.risks)} risk(s)",
        )
        return review.markdown
    except Exception as e:
        return f"Error: {e}"


@tool("save_skill", parse_docstring=True)
async def save_skill_tool(
    runtime: Runtime,
    description: str,
    name: str,
    path: str = "",
) -> str:
    """Save a skill you built into the GLOBAL skill registry for reuse in future chats.

    When you create a reusable skill (a SKILL.md plus any helper scripts) in the
    workspace, call this to persist it globally — it survives this sandbox and
    appears as `/<name>` in every future conversation and in the Agent's Computer
    skill launcher. Prefer this over installing skills with `npx skills add`, which
    is ephemeral (lost when the sandbox is recycled).

    Args:
        description: Why you are saving this skill. ALWAYS PROVIDE THIS FIRST.
        name: Skill name in hyphen-case (e.g. "hono-api"). Becomes /<name>.
        path: Workspace dir holding the skill's SKILL.md. Defaults to /mnt/user-data/workspace/<name>.
    """
    try:
        from deerflow.runtime.user_context import get_effective_user_id
        from deerflow.skills.promote import promote_skill_to_global

        sandbox_id = _get_sandbox_id(runtime)
        thread_id = _thread_id_from_sandbox_id(sandbox_id) or _extract_thread_id_from_thread_data(get_thread_data(runtime))
        if not thread_id:
            return "Error: saving a skill requires a per-thread sandbox."

        rel = (path or f"{_VIRTUAL_WORKSPACE}/{name}").replace(_VIRTUAL_WORKSPACE, "").strip("/")
        if is_local_sandbox(runtime):
            host_root = _resolve_workspace_host_path(runtime)
        else:
            from deerflow.config.paths import get_paths

            try:
                user_id = get_effective_user_id()
            except Exception:
                user_id = None
            host_root = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
        if host_root is None:
            return "Error: workspace not found."
        source_dir = host_root / rel if rel else host_root

        result = await promote_skill_to_global(name, source_dir, thread_id=thread_id)
        _write_sandbox_observation(
            sandbox_id,
            "save_skill",
            str(source_dir),
            f"Save skill '{name}': {'ok' if result.get('saved') else result.get('reason')}",
        )
        if not result.get("saved"):
            return f"Could not save skill '{name}': {result.get('reason')}"
        return f"✓ Saved skill '{name}' to the global registry ({len(result.get('files', []))} file(s)). It's now available as /{name} in all future chats."
    except Exception as e:
        return f"Error: {e}"


@tool("browser_check", parse_docstring=True)
def browser_check_tool(
    runtime: Runtime,
    description: str,
    routes: str = "/",
) -> str:
    """Self-test the running app in a real browser and report what you see.

    Loads your live dev server in the sandbox's built-in Chromium, then reports
    console errors, render failures, and whether each page loaded. Use this AFTER
    starting the dev server to verify the app actually works before telling the
    user it's done — and to find bugs to fix.

    Args:
        description: Why you are testing. ALWAYS PROVIDE THIS FIRST.
        routes: Comma-separated routes to check, e.g. "/, /about, /dashboard". Defaults to "/".
    """
    try:
        from deerflow.sandbox.browser_check import run_browser_check

        sandbox_id = _get_sandbox_id(runtime)
        if is_local_sandbox(runtime):
            return "Error: browser self-test requires the container (AIO) sandbox."
        thread_id = _extract_thread_id_from_thread_data(get_thread_data(runtime))
        if not thread_id or not sandbox_id:
            return "Error: browser self-test requires a per-thread sandbox."
        sandbox = get_sandbox_provider().get(sandbox_id)
        if sandbox is None:
            return "Error: sandbox not available."
        route_list = [r.strip() for r in routes.split(",") if r.strip()] or ["/"]
        check = run_browser_check(thread_id, sandbox, routes=route_list, with_screenshot=False)
        _write_sandbox_observation(
            sandbox_id,
            "browser_check",
            None,
            f"Browser self-test: {'pass' if check.ok else 'issues'} ({len(check.routes)} route(s))",
        )
        return check.summary()
    except Exception as e:
        return f"Error: {e}"


@tool("stop_dev_server", parse_docstring=True)
async def stop_dev_server_tool(
    runtime: Runtime,
    description: str,
    label: str = "app",
) -> str:
    """Stop a running dev server for this conversation.

    Args:
        description: Explain why you are stopping the server. ALWAYS PROVIDE THIS FIRST.
        label: Which server to stop when running more than one (default "app").
    """
    try:
        from deerflow.sandbox.dev_server import stop_dev_server as _stop

        sandbox_id = _get_sandbox_id(runtime)
        if is_local_sandbox(runtime):
            thread_id = _thread_id_from_sandbox_id(sandbox_id)
        else:
            thread_id = _extract_thread_id_from_thread_data(get_thread_data(runtime))
        if not thread_id:
            return "No dev server to stop."
        stopped = await _stop(thread_id, label or "app")
        _write_sandbox_observation(sandbox_id, "stop_dev_server", None, "Dev server stopped" if stopped else "No server running")
        return "✓ Dev server stopped." if stopped else "No dev server was running."
    except Exception as e:
        return f"Error: {e}"
