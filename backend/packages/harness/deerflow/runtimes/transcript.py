"""Turn a LangChain message list into the prompt an ACP agent receives.

ACP agents keep their own session state per process; Nova spawns a fresh
adapter per turn, so the prompt carries a bounded tail of the conversation
followed by the request itself.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage


def _text(msg: AnyMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def transcript_for_prompt(messages: list[AnyMessage], *, max_prior_turns: int = 6, max_chars: int = 12000) -> str:
    """The last human message, preceded by up to ``max_prior_turns``
    (human, assistant) exchanges as plain text."""
    humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not humans:
        return ""
    last = humans[-1]
    request = _text(messages[last]).strip()
    prior = [m for m in messages[:last] if isinstance(m, (HumanMessage, AIMessage))]
    turns: list[str] = []
    for m in prior:
        t = _text(m).strip()
        if not t:
            continue
        turns.append(("User: " if isinstance(m, HumanMessage) else "Assistant: ") + t)
    # keep the tail: max_prior_turns exchanges ≈ 2 messages each
    turns = turns[-(2 * max_prior_turns) :]
    body = "\n\n".join(turns)
    if len(body) > max_chars:
        body = "…" + body[-max_chars:]
    if not body:
        return request
    return f"Earlier in this conversation:\n\n{body}\n\n---\n\n{request}"


#: Tools the sandbox capability module exposes over Nova's MCP server.
SANDBOX_TOOL_NAMES: tuple[str, ...] = ("sandbox__bash", "sandbox__read_file", "sandbox__write_file", "sandbox__str_replace", "sandbox__ls", "sandbox__glob", "sandbox__grep")


def runtime_preamble(mcp_server_names: list[str] | tuple[str, ...]) -> str:
    """Tell an ACP runtime where its hands are.

    Claude Code arrives with its own Bash/Read/Write. Inside Nova's gateway
    container its Bash cannot run at all (``~/.claude`` is mounted read-only
    so it cannot create a session directory — deliberately: that shell would
    execute in the gateway, not in the user's sandbox), and its file tools
    only see its private ``acp-workspace``. Left to itself it reported
    "Bash is blocked" and worked in that private directory while the user's
    Agent's Computer showed nothing (live 2026-09-22). The user's sandbox is
    the ``nova`` MCP server; say so, once, at the top of every turn.

    Returns "" when Nova's server is not mounted — there is nothing to point at.
    """
    if "nova" not in mcp_server_names:
        return ""
    tools = ", ".join(f"`{name}`" for name in SANDBOX_TOOL_NAMES)
    return (
        "You are running as the runtime for a Nova chat. The user's Agent's Computer — the sandbox they are watching — is the `nova` MCP server. "
        f"Use its sandbox tools for every shell and file action: {tools}. Files live under /mnt/user-data/workspace (your work), /mnt/user-data/uploads (their files) and /mnt/user-data/outputs (deliverables). "
        "Everything you do through those tools appears live in the user's Terminal, Files and Activity panes. "
        "Your own built-in Bash is unavailable in this environment and your built-in file tools only see a private scratch directory the user cannot see — do not use them for the user's work. "
        "For parallel or delegated work prefer your own subagents (they run on your model and see the same `nova` server); Nova's `agents__delegate` runs a Nova subagent as a background job on Nova's configured model — use it only when a Nova-specific agent is what the task needs. "
        "The `nova` server also exposes Nova's platform (jobs, integrations, agents, models, skills); use those when the task calls for them."
    )
