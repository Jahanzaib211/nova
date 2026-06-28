"""Verify-screenshot → model-vision injection tests.

The deterministic verify path stashes a rendered screenshot per thread; the
vision-gated ViewImageMiddleware injects it ONE-SHOT and TRANSIENTLY so the model
can SEE its build. These tests use synthetic data only — no project specifics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage

from deerflow.agents.middlewares import verify_vision as vv
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware


def test_stash_and_pop_is_one_shot():
    vv.stash_verify_screenshot("t1", "QUJD")  # base64 of "ABC"
    first = vv.pop_verify_screenshot("t1")
    assert first is not None and first["base64"] == "QUJD" and first["mime"] == "image/png"
    # second pop is empty — one-shot
    assert vv.pop_verify_screenshot("t1") is None


def test_stash_drops_oversized():
    vv._pending.pop("t-big", None)
    vv.stash_verify_screenshot("t-big", "x" * (vv._MAX_SCREENSHOT_B64 + 1))
    assert vv.pop_verify_screenshot("t-big") is None


def test_stash_noop_on_empty_inputs():
    vv._pending.pop("t-empty", None)
    vv.stash_verify_screenshot(None, "QUJD")
    vv.stash_verify_screenshot("t-empty", None)
    vv.stash_verify_screenshot("t-empty", "")
    assert vv.pop_verify_screenshot("t-empty") is None


@dataclass
class _Route:
    screenshot_b64: str | None = None


@dataclass
class _Check:
    routes: list = field(default_factory=list)


def test_first_route_screenshot():
    assert vv._first_route_screenshot(_Check(routes=[_Route(None), _Route("ZZZ")])) == "ZZZ"
    assert vv._first_route_screenshot(_Check(routes=[])) is None


class _FakeRuntime:
    def __init__(self, ctx: dict) -> None:
        self.context = ctx


class _FakeRequest:
    def __init__(self, messages: list, runtime: _FakeRuntime) -> None:
        self.messages = messages
        self.runtime = runtime

    def override(self, *, messages: list) -> "_FakeRequest":
        self.messages = messages
        return self


def test_inject_appends_image_for_vision_model():
    vv.stash_verify_screenshot("t-inj", "QUJD")
    mw = ViewImageMiddleware()
    req = _FakeRequest([HumanMessage(content="go")], _FakeRuntime({"thread_id": "t-inj"}))

    out = mw._inject_verify_screenshot(req)

    last = out.messages[-1]
    assert isinstance(last, HumanMessage)
    assert last.name == "verify_screenshot"
    assert last.additional_kwargs.get("hide_from_ui") is True
    # mixed content: a text block + an image_url block carrying the screenshot
    kinds = [b.get("type") for b in last.content if isinstance(b, dict)]
    assert "image_url" in kinds and "text" in kinds
    img = next(b for b in last.content if isinstance(b, dict) and b["type"] == "image_url")
    assert "base64,QUJD" in img["image_url"]["url"]
    # one-shot: a second model call gets nothing appended
    again = mw._inject_verify_screenshot(_FakeRequest([HumanMessage(content="go")], _FakeRuntime({"thread_id": "t-inj"})))
    assert len(again.messages) == 1


def test_inject_noop_when_no_screenshot():
    vv._pending.pop("t-none", None)
    mw = ViewImageMiddleware()
    req = _FakeRequest([HumanMessage(content="hi")], _FakeRuntime({"thread_id": "t-none"}))
    out = mw._inject_verify_screenshot(req)
    assert len(out.messages) == 1 and out.messages[0].content == "hi"
