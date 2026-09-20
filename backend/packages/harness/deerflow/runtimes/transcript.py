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
