"""Sandbox: the Agent's Computer as capability operations.

The lead agent has always had ``bash``, ``ls``, ``glob``, ``grep``,
``read_file``, ``write_file`` and ``str_replace`` bound directly as LangChain
tools. Nothing declared them as *capabilities*, so they reached exactly one
surface — and an external harness driving Nova over ``/api/mcp/nova`` (Claude
Code through the ACP adapter, OpenClaw) could manage jobs and integrations but
could not touch the sandbox at all. That asymmetry is why the ACP path felt
like a sidecar next to the native one.

Declaring them here mirrors the native layer onto the MCP surface: one
declaration, reachable by the lead agent, the UI and an external harness, the
same way every other module works.

**Every operation is thread-scoped.** ``OpContext.thread_id`` names the
sandbox to act in; without it there is no Agent's Computer to drive and the
call is refused rather than silently landing somewhere else. For an MCP caller
that id comes from the per-turn runtime token (``runtime:<thread_id>``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Items, section_enabled
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


class _ThreadScoped(BaseModel):
    """Inputs may name a thread explicitly; otherwise the context supplies it."""

    thread_id: str | None = Field(default=None, description="Thread whose sandbox to act in. Defaults to the caller's own thread.")


class BashIn(_ThreadScoped):
    command: str = Field(description="Shell command to run inside the thread's sandbox container.")
    timeout_seconds: int = Field(default=120, ge=1, le=1800)


class PathIn(_ThreadScoped):
    path: str = Field(description="Absolute path inside the sandbox, e.g. /mnt/user-data/workspace/app.py")


class WriteIn(PathIn):
    content: str = Field(description="Full new file contents.")


class ReplaceIn(PathIn):
    old_str: str = Field(description="Exact text to replace; must occur exactly once.")
    new_str: str = Field(description="Replacement text.")


class SearchIn(_ThreadScoped):
    pattern: str = Field(description="Glob pattern (glob) or regular expression (grep).")
    path: str = Field(default="/mnt/user-data", description="Directory to search under.")
    max_results: int = Field(default=100, ge=1, le=1000)


class TextOut(BaseModel):
    output: str
    truncated: bool = False


class FileOut(BaseModel):
    path: str
    content: str
    exists: bool


class WriteOut(BaseModel):
    path: str
    bytes_written: int


def _describe(ctx: OpContext, what: str) -> str:
    """Audit-trail line for the action.

    These tools record every call into the thread's own audit log, which the
    Agent's Computer renders. Naming the surface keeps a harness-driven action
    distinguishable from one the lead agent took on its own.
    """
    return f"[{ctx.surface}] {what}"


def _thread(ctx: OpContext, inp: _ThreadScoped) -> str:
    """The thread to act in, or a refusal naming why there is none.

    An explicit ``thread_id`` wins so a caller can act across its own threads;
    otherwise the context's. A missing id is an error rather than a default,
    because "no thread" has no sensible sandbox to fall back to — picking one
    would be acting on data the caller did not name.
    """
    thread_id = (inp.thread_id or ctx.thread_id or "").strip()
    if not thread_id:
        raise ValueError("No thread in scope. Pass thread_id, or call with a per-turn runtime token (minted as runtime:<thread_id>) so the sandbox to act in is unambiguous.")
    return thread_id


#: Capability op -> the module-level symbol in ``deerflow.sandbox.tools``.
#: The decorator names ("bash") and the Python names ("bash_tool") differ, and
#: only the latter can be resolved with getattr.
_TOOL_SYMBOLS = {
    "bash": "bash_tool",
    "ls": "ls_tool",
    "glob": "glob_tool",
    "grep": "grep_tool",
    "read_file": "read_file_tool",
    "write_file": "write_file_tool",
    "str_replace": "str_replace_tool",
}


def _thread_data(thread_id: str, user_id: str | None) -> dict[str, str]:
    """The thread's data directories, created if absent -- the same three
    paths ``ThreadDataMiddleware`` computes for the lead agent."""
    from deerflow.config.paths import get_paths

    paths = get_paths()
    paths.ensure_thread_dirs(thread_id, user_id=user_id)
    return {
        "workspace_path": str(paths.sandbox_work_dir(thread_id, user_id=user_id)),
        "uploads_path": str(paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
        "outputs_path": str(paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
    }


class _ToolRuntime:
    """The slice of LangGraph's ``Runtime`` these tools actually read.

    They only ever touch ``runtime.context`` -- a plain dict carrying
    ``thread_id`` and, once a sandbox is provisioned, ``sandbox_id`` (which
    the tools write back so ``after_agent`` can release it). Supplying that
    directly is what lets a capability call reuse the lead agent's own tools
    rather than reimplementing path containment, the command classifier and
    the audit trail beside them.
    """

    def __init__(self, thread_id: str, user_id: str | None) -> None:
        self.context: dict[str, Any] = {"thread_id": thread_id}
        if user_id:
            self.context["user_id"] = user_id
        # `state` is not optional: the tools read `state["sandbox"]` and write
        # the acquired sandbox_id back into it, so a None here fails with
        # "Tool runtime state not available". A fresh dict is correct rather
        # than merely sufficient -- acquisition is keyed by thread_id, so an
        # empty state resolves to that thread's existing sandbox instead of
        # creating a second one.
        #
        # `thread_data` is what ThreadDataMiddleware puts in the lead agent's
        # state before any tool runs: the thread's workspace/uploads/outputs
        # paths. On the local provider `bash`, `read_file` and friends
        # validate every path against it and refuse with "Thread data not
        # available for local sandbox" when it is missing -- so without it
        # every harness-driven sandbox call failed on a local deployment
        # while the same call from the lead agent worked (found by
        # tests/test_acp_runtime_drives_sandbox_e2e.py).
        self.state: dict[str, Any] = {"thread_data": _thread_data(thread_id, user_id)}
        # These three are genuinely None-guarded at every use site.
        self.config = None
        self.store = None
        self.user_context = None


async def _invoke(tool_name: str, thread_id: str, payload: dict[str, Any]) -> str:
    """Run one sandbox tool in the thread's own sandbox.

    Calls the tool's underlying function rather than ``ainvoke``: the runtime
    is an injected argument, not part of the tool's declared input schema, so
    there is no way to pass it through the normal invocation path.
    """
    import asyncio
    import inspect

    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sandbox import tools as sandbox_tools

    symbol = _TOOL_SYMBOLS.get(tool_name)
    tool = getattr(sandbox_tools, symbol, None) if symbol else None
    if tool is None:
        raise ValueError(f"sandbox tool {tool_name!r} is not available")

    func = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
    if func is None:
        raise ValueError(f"sandbox tool {tool_name!r} exposes no callable")

    runtime = _ToolRuntime(thread_id, get_effective_user_id())
    kwargs = {"runtime": runtime, **payload}
    # Drop anything this particular tool does not declare, so one shared
    # caller can serve tools with different signatures.
    try:
        accepted = set(inspect.signature(func).parameters)
        kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    except (TypeError, ValueError):
        pass

    result = func(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    elif asyncio.iscoroutinefunction(func):  # pragma: no cover - defensive
        result = await result
    return result if isinstance(result, str) else str(result)


async def _bash(ctx: OpContext, inp: BashIn) -> TextOut:
    out = await _invoke(
        "bash",
        _thread(ctx, inp),
        {"command": inp.command, "timeout": inp.timeout_seconds, "description": _describe(ctx, f"run: {inp.command[:80]}")},
    )
    return TextOut(output=out)


async def _read(ctx: OpContext, inp: PathIn) -> FileOut:
    out = await _invoke("read_file", _thread(ctx, inp), {"path": inp.path, "description": _describe(ctx, f"read {inp.path}")})
    return FileOut(path=inp.path, content=out, exists=bool(out))


async def _write(ctx: OpContext, inp: WriteIn) -> WriteOut:
    await _invoke("write_file", _thread(ctx, inp), {"path": inp.path, "content": inp.content, "description": _describe(ctx, f"write {inp.path}")})
    return WriteOut(path=inp.path, bytes_written=len(inp.content.encode("utf-8")))


async def _replace(ctx: OpContext, inp: ReplaceIn) -> TextOut:
    out = await _invoke(
        "str_replace",
        _thread(ctx, inp),
        {"path": inp.path, "old_str": inp.old_str, "new_str": inp.new_str, "description": _describe(ctx, f"edit {inp.path}")},
    )
    return TextOut(output=out)


async def _ls(ctx: OpContext, inp: PathIn) -> Items:
    out = await _invoke("ls", _thread(ctx, inp), {"path": inp.path, "description": _describe(ctx, f"list {inp.path}")})
    lines = [line for line in out.splitlines() if line.strip()]
    return Items(items=[{"entry": line} for line in lines], total=len(lines))


async def _glob(ctx: OpContext, inp: SearchIn) -> Items:
    out = await _invoke("glob", _thread(ctx, inp), {"pattern": inp.pattern, "path": inp.path, "description": _describe(ctx, f"glob {inp.pattern}")})
    lines = [line for line in out.splitlines() if line.strip()][: inp.max_results]
    return Items(items=[{"path": line} for line in lines], total=len(lines))


async def _grep(ctx: OpContext, inp: SearchIn) -> Items:
    out = await _invoke("grep", _thread(ctx, inp), {"pattern": inp.pattern, "path": inp.path, "description": _describe(ctx, f"grep {inp.pattern}")})
    lines = [line for line in out.splitlines() if line.strip()][: inp.max_results]
    return Items(items=[{"match": line} for line in lines], total=len(lines))


async def _status() -> ModuleStatus:
    if not section_enabled("sandbox"):
        return ModuleStatus(configured=False, healthy=False, detail="sandbox section is disabled")
    try:
        from deerflow.config.app_config import get_app_config

        image = getattr(getattr(get_app_config(), "sandbox", None), "image", "") or ""
    except Exception as exc:  # noqa: BLE001 - status must never raise
        return ModuleStatus(configured=True, healthy=False, detail=f"config unreadable: {exc}")
    if not image:
        return ModuleStatus(configured=True, healthy=False, detail="sandbox.image is not set")
    return ModuleStatus(configured=True, healthy=True, detail=f"image {image}")


MODULE = CapabilityModule(
    id="sandbox",
    title="Agent's Computer",
    flag="sandbox",
    config_key="sandbox",
    description="The per-thread sandbox: run commands, read and write files, search. The same tools the lead agent uses, reachable by an external harness.",
    status=_status,
    operations=[
        Operation(name="sandbox.bash", kind="execute", input=BashIn, output=TextOut, handler=_bash, description="Run a shell command inside the thread's sandbox."),
        Operation(name="sandbox.read_file", kind="read", input=PathIn, output=FileOut, handler=_read, description="Read a file from the thread's sandbox."),
        Operation(name="sandbox.write_file", kind="write", input=WriteIn, output=WriteOut, handler=_write, description="Create or overwrite a file in the thread's sandbox."),
        Operation(name="sandbox.str_replace", kind="write", input=ReplaceIn, output=TextOut, handler=_replace, description="Replace one exact, unique string in a sandbox file."),
        Operation(name="sandbox.ls", kind="read", input=PathIn, output=Items, handler=_ls, description="List a directory in the thread's sandbox."),
        Operation(name="sandbox.glob", kind="read", input=SearchIn, output=Items, handler=_glob, description="Find files by glob pattern in the thread's sandbox."),
        Operation(name="sandbox.grep", kind="read", input=SearchIn, output=Items, handler=_grep, description="Search file contents by regex in the thread's sandbox."),
    ],
)
