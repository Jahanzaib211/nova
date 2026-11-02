"""Strip synthetic error-fallback messages before they reach the model.

Companion serializer filter:
    ``deerflow.runtime.serialization.strip_deerflow_error_fallback_messages``

When ``LLMErrorHandlingMiddleware`` injects a synthetic AIMessage for a
quota / auth / transient failure, the message persists in thread state and
gets fed back to the model on the next resume.  The model then echoes the
error string as its own answer (finish_reason: ``stop``, output_tokens ==
exact error string length), contaminating the thread indefinitely.

This middleware is the LLM-side counterpart to the serializer filter.  It
runs in ``before_model`` and drops any message whose ``additional_kwargs``
contains ``deerflow_error_fallback == True`` from the messages list before
the model sees it.

Why both layers:
    - The serializer filter (``runtime/serialization.py``) handles the
      UI/REST path — keeps the synthetic message out of chat history and
      out of API responses.
    - This middleware handles the LLM input path — keeps the synthetic
      message out of the model's context on the next resume.

Either layer alone is sufficient for its own concern.  Both together is
defense in depth: even if one is bypassed by a future change, the other
still cleans the contamination.

Non-fatal by construction:
    - All exception paths wrapped in try/except.
    - ``_strip_error_messages`` is a pure function — if it raises for any
      reason, the original state passes through unchanged.
"""

from __future__ import annotations

import logging
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class StripErrorFallbackMiddleware(AgentMiddleware[AgentState]):
    """Drop ``deerflow_error_fallback`` messages from the model input.

    Runs in ``before_model`` and returns a state update with the filtered
    messages list if any contamination was found.  Returns ``None`` when
    no filtering is needed (the common path).

    Stateless: no caching, no thread-id bookkeeping.  Filtering cost is
    O(N) over the messages list, which is fine for the typical thread
    history size.
    """

    @staticmethod
    def _strip_error_messages(messages: list) -> list:
        """Pure function: drop messages with deerflow_error_fallback kwarg.

        Handles both dict-serialized messages (LangChain ``model_dump``
        output) and in-process LangChain message objects (which carry
        ``.additional_kwargs`` as a dict or None).
        """
        from deerflow.runtime.serialization import strip_deerflow_error_fallback_messages

        return strip_deerflow_error_fallback_messages(messages)

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        try:
            messages = state.get("messages", []) if hasattr(state, "get") else []
            if not messages:
                return None

            filtered = self._strip_error_messages(messages)
            if len(filtered) == len(messages):
                return None  # common path: no contamination

            logger.debug(
                "StripErrorFallbackMiddleware: stripped %d error-fallback message(s) from model input",
                len(messages) - len(filtered),
            )
            return {"messages": filtered}
        except Exception:  # noqa: BLE001 — non-fatal middleware
            logger.debug("StripErrorFallbackMiddleware: before_model failed", exc_info=True)
            return None

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.before_model(state, runtime)


__all__ = ["StripErrorFallbackMiddleware"]
