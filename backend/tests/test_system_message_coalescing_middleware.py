"""Tests for SystemMessageCoalescingMiddleware.

Hermetic: uses a duck-typed stand-in for ModelRequest (the middleware only
touches .messages, .system_message, and .override) so no agent/runtime
scaffolding is needed.
"""

from dataclasses import dataclass, replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deerflow.agents.middlewares.system_message_coalescing_middleware import (
    SystemMessageCoalescingMiddleware,
)


@dataclass
class FakeRequest:
    messages: list[Any]
    system_message: SystemMessage | None = None

    def override(self, **kwargs) -> "FakeRequest":
        return replace(self, **kwargs)


def test_no_stray_system_messages_is_passthrough() -> None:
    req = FakeRequest(messages=[HumanMessage("hi")], system_message=SystemMessage("base"))
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert out is req


def test_stray_system_message_merges_into_leading_system() -> None:
    req = FakeRequest(
        messages=[SystemMessage("manifest primer"), HumanMessage("build it"), AIMessage("ok")],
        system_message=SystemMessage("base prompt"),
    )
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert all(not isinstance(m, SystemMessage) for m in out.messages)
    assert len(out.messages) == 2
    assert out.system_message.content == "base prompt\n\nmanifest primer"


def test_stray_system_with_no_base_becomes_the_system_message() -> None:
    req = FakeRequest(messages=[SystemMessage("only stray"), HumanMessage("q")], system_message=None)
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert out.system_message.content == "only stray"
    assert len(out.messages) == 1


def test_multiple_strays_merge_in_order() -> None:
    req = FakeRequest(
        messages=[SystemMessage("a"), HumanMessage("q"), SystemMessage("b")],
        system_message=SystemMessage("base"),
    )
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert out.system_message.content == "base\n\na\n\nb"


def test_block_list_content_is_extracted() -> None:
    req = FakeRequest(
        messages=[SystemMessage(content=[{"type": "text", "text": "from blocks"}]), HumanMessage("q")],
        system_message=None,
    )
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert out.system_message.content == "from blocks"


def test_failure_is_fail_open() -> None:
    class Broken:
        # .messages raises — middleware must return the request unchanged
        @property
        def messages(self):
            raise RuntimeError("boom")

    req = Broken()
    out = SystemMessageCoalescingMiddleware()._coalesce(req)
    assert out is req
