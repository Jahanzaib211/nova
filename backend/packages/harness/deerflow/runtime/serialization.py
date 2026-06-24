"""Canonical serialization for LangChain / LangGraph objects.

Provides a single source of truth for converting LangChain message
objects, Pydantic models, and LangGraph state dicts into plain
JSON-serialisable Python structures.

Consumers: ``deerflow.runtime.runs.worker`` (SSE publishing) and
``app.gateway.routers.threads`` (REST responses).
"""

from __future__ import annotations

from typing import Any


def serialize_lc_object(obj: Any) -> Any:
    """Recursively serialize a LangChain object to a JSON-serialisable dict."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: serialize_lc_object(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_lc_object(item) for item in obj]
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # Pydantic v1 / older objects
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    # Interrupt is a __slots__ class — no model_dump/dict/__dict__, so it
    # would reach str() and produce a malformed payload.
    try:
        from langgraph.types import Interrupt
    except ImportError:
        pass
    else:
        if isinstance(obj, Interrupt):
            return serialize_lc_object(
                {
                    "value": obj.value,
                    "id": getattr(obj, "id", None),
                }
            )
    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def serialize_channel_values(channel_values: dict[str, Any]) -> dict[str, Any]:
    """Serialize channel values, stripping internal LangGraph keys.

    Only ``__pregel_*`` keys are removed — ``__interrupt__`` is deliberately
    preserved so the LangGraph SDK can detect interrupt events from values
    chunks (see issue #3595).
    """
    result: dict[str, Any] = {}
    for key, value in channel_values.items():
        if key.startswith("__pregel_"):
            continue
        result[key] = serialize_lc_object(value)
    return result


def strip_data_url_image_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove ``data:``-scheme ``image_url`` blocks from *hide_from_ui* messages.

    The history and run-wait endpoints return checkpoint-persisted messages to
    the frontend.  ``ViewImageMiddleware`` stores full base64 image payloads in
    ``hide_from_ui`` human messages — these are internal model context and must
    not be sent over the wire (huge response bodies, no UI value).

    Only content blocks of type ``image_url`` whose URL starts with ``data:``
    are stripped.  Text blocks, ``https://`` image URLs, and non-hidden
    messages are left untouched so that message ordering and count are
    preserved.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue

        # Only touch messages explicitly flagged as hidden from the UI.
        additional_kwargs = msg.get("additional_kwargs")
        if not (isinstance(additional_kwargs, dict) and additional_kwargs.get("hide_from_ui") is True):
            result.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        # Filter out image_url blocks with data: scheme.
        filtered = [block for block in content if not (isinstance(block, dict) and block.get("type") == "image_url" and isinstance(block.get("image_url"), dict) and str(block["image_url"].get("url", "")).startswith("data:"))]
        result.append({**msg, "content": filtered})
    return result


def strip_deerflow_error_fallback_messages(messages: list[Any]) -> list[Any]:
    """Drop synthetic error-fallback messages from a message list.

    ``LLMErrorHandlingMiddleware`` injects a synthetic AIMessage when the LLM
    provider rejects the request (quota / auth / transient).  The message
    carries ``additional_kwargs.deerflow_error_fallback == True``.  Without
    this filter the message persists in thread state, gets fed back to the
    LLM on the next resume (the model then echoes it as its own answer), and
    also shows up in the user's chat history.

    This filter is the canonical seam: any caller serializing messages for
    the LLM context or for the UI history must run their list through it
    first so the contamination never reaches either consumer.

    Behaviour:
        - Messages with ``additional_kwargs.deerflow_error_fallback == True``
          are dropped, regardless of message type (HumanMessage / AIMessage /
          ToolMessage / SystemMessage).
        - All other messages pass through unchanged. Ordering preserved.
        - Non-dict items (raw LangChain message objects) are inspected via
          ``.additional_kwargs`` attribute, so the filter works on both
          dict-serialized and in-process LangChain objects.
        - Defensive: any structural oddity (missing kwargs, wrong type,
          etc.) preserves the message rather than dropping it.

    The UI-side signal that "something went wrong" is provided separately by
    the ``llm_error`` stream event (see C2 of the v6 sprint), so dropping
    the message from the chat history is safe.
    """
    result: list[Any] = []
    for msg in messages:
        additional_kwargs: Any = None
        if isinstance(msg, dict):
            additional_kwargs = msg.get("additional_kwargs")
        else:
            additional_kwargs = getattr(msg, "additional_kwargs", None)

        if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback") is True:
            continue
        result.append(msg)
    return result


def serialize_channel_values_for_api(channel_values: dict[str, Any]) -> dict[str, Any]:
    """Serialize channel values and strip base64 image data + error fallbacks.

    Convenience wrapper combining :func:`serialize_channel_values` with
    :func:`strip_data_url_image_blocks` and
    :func:`strip_deerflow_error_fallback_messages`.  Use this in all REST
    endpoints that return channel values to the frontend so that
    ``data:``-scheme base64 image payloads and synthetic LLM error
    fallbacks are never sent over the wire.
    """
    result = serialize_channel_values(channel_values)
    if isinstance(result.get("messages"), list):
        result["messages"] = strip_data_url_image_blocks(result["messages"])
        result["messages"] = strip_deerflow_error_fallback_messages(result["messages"])
    return result


def serialize_messages_tuple(obj: Any) -> Any:
    """Serialize a messages-mode tuple ``(chunk, metadata)``."""
    if isinstance(obj, tuple) and len(obj) == 2:
        chunk, metadata = obj
        return [serialize_lc_object(chunk), metadata if isinstance(metadata, dict) else {}]
    return serialize_lc_object(obj)


def serialize(obj: Any, *, mode: str = "") -> Any:
    """Serialize LangChain objects with mode-specific handling.

    * ``messages`` — obj is ``(message_chunk, metadata_dict)``
    * ``values`` — obj is the full state dict; ``__pregel_*`` keys stripped and
      base64 ``data:`` image blocks dropped from hide_from_ui messages
    * everything else — recursive ``model_dump()`` / ``dict()`` fallback
    """
    if mode == "messages":
        return serialize_messages_tuple(obj)
    if mode == "values":
        # ``values`` snapshots stream the full state to the frontend, so they
        # must drop base64 image payloads the same way the REST endpoints do.
        return serialize_channel_values_for_api(obj) if isinstance(obj, dict) else serialize_lc_object(obj)
    return serialize_lc_object(obj)
