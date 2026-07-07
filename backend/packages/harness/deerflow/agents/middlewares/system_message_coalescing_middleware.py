"""Coalesce stray system messages into the request's single leading system message.

Several Nova middlewares inject ``SystemMessage`` entries into the *message
list* (e.g. the agent-manifest primer prepended by ``ThreadDataMiddleware``).
Hosted providers tolerate system-role messages at any position, but strict
llama.cpp chat templates reject them at request time::

    Error code: 400 — Jinja Exception: System message must be at the beginning.

This middleware runs innermost (registered last, so it sees every earlier
middleware's injections) and normalizes the request: any ``SystemMessage``
found in ``request.messages`` is removed and its text appended to
``request.system_message``, so the model always receives exactly one system
message, at position 0. Semantically equivalent for compliant providers,
required for strict local backends.

Non-fatal by construction: any failure returns the original request unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)


def _text_of(content: Any) -> str:
    """Best-effort text extraction from a message content payload."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


class SystemMessageCoalescingMiddleware(AgentMiddleware):
    """Fold every ``SystemMessage`` in ``request.messages`` into ``request.system_message``."""

    def _coalesce(self, request: ModelRequest) -> ModelRequest:
        try:
            strays = [m for m in request.messages if isinstance(m, SystemMessage)]
            if not strays:
                return request
            kept = [m for m in request.messages if not isinstance(m, SystemMessage)]
            segments: list[str] = []
            base = request.system_message
            if base is not None:
                base_text = _text_of(base.content)
                if base_text:
                    segments.append(base_text)
            segments.extend(t for t in (_text_of(m.content) for m in strays) if t)
            merged = SystemMessage(content="\n\n".join(segments))
            logger.debug("coalesced %d stray system message(s) into the leading system message", len(strays))
            return request.override(system_message=merged, messages=kept)
        except Exception:  # noqa: BLE001 — normalization must never kill the run
            logger.exception("system-message coalescing failed; sending request unchanged")
            return request

    @override
    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._coalesce(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler):
        return await handler(self._coalesce(request))
