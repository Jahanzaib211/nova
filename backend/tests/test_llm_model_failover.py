"""A provider that runs dry hands the turn to the next configured model.

Live 2026-10-30: a customer's three messages all failed in ~4 s. The default
model answered ``400 You're out of extra usage`` (a Claude login past its
quota), MiniMax answered ``402 insufficient balance``, and nothing tried the
other. That message also matched none of the quota patterns, so it was
reported as a generic failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig

CLAUDE_DRY = "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': \"You're out of extra usage. Add more at claude.ai/settings/usage and keep going.\"}}"
MINIMAX_DRY = "Error code: 402 - {'type': 'error', 'error': {'type': 'insufficient_balance_error', 'message': 'insufficient balance (1008)', 'http_code': '402'}}"
FIREWORKS_DRY = "Error code: 412 - {'error': {'message': 'Account x is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.'}}"


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers={})


class Model:
    """Stands in for a chat model; ``model_name`` is what the factory sets."""

    def __init__(self, name: str) -> None:
        self.model_name = name


class Request:
    def __init__(self, model: Model) -> None:
        self.model = model

    def override(self, **kw):
        return Request(kw.get("model", self.model))


def _middleware(fallbacks: list[str]) -> LLMErrorHandlingMiddleware:
    from deerflow.config.model_config import ModelConfig

    models = [ModelConfig(name=n, display_name=n, use="langchain_openai:ChatOpenAI", model=f"id-{n}") for n in ("primary", "backup", "last")]
    cfg = AppConfig(sandbox=SandboxConfig(use="test"), models=models, model_fallbacks=fallbacks)
    mw = LLMErrorHandlingMiddleware(app_config=cfg)
    mw.retry_base_delay_ms = 0
    mw.retry_cap_delay_ms = 0
    return mw


@pytest.fixture(autouse=True)
def _factory(monkeypatch):
    import deerflow.models as models_pkg

    built: list[str] = []

    def _create(name=None, thinking_enabled=False, *, app_config=None, **kw):
        built.append(name)
        return Model(f"id-{name}")

    monkeypatch.setattr(models_pkg, "create_chat_model", _create)
    monkeypatch.setattr("langgraph.config.get_stream_writer", lambda: lambda _e: None)
    return built


def _handler(answers: dict[str, object], seen: list[str]):
    async def handler(request: Request) -> AIMessage:
        name = request.model.model_name
        seen.append(name)
        outcome = answers[name]
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=str(outcome))

    return handler


@pytest.mark.parametrize("message,status", [(CLAUDE_DRY, 400), (MINIMAX_DRY, 402), (FIREWORKS_DRY, 412)])
def test_every_observed_out_of_credit_error_is_quota(message, status):
    retriable, reason = _middleware([])._classify_error(ProviderError(message, status))
    assert (retriable, reason) == (False, "quota")


def test_a_dry_provider_hands_the_turn_to_the_next_model(_factory):
    seen: list[str] = []
    handler = _handler({"id-primary": ProviderError(CLAUDE_DRY, 400), "id-backup": "answered by backup"}, seen)
    result = asyncio.run(_middleware(["primary", "backup"]).awrap_model_call(Request(Model("id-primary")), handler))
    assert result.content == "answered by backup"
    assert seen == ["id-primary", "id-backup"]
    assert _factory == ["backup"]


def test_the_sync_path_fails_over_too():
    seen: list[str] = []
    handler_async = _handler({"id-primary": ProviderError(MINIMAX_DRY, 402), "id-backup": "sync backup"}, seen)

    def handler(request):
        return asyncio.run(handler_async(request))

    result = _middleware(["primary", "backup"]).wrap_model_call(Request(Model("id-primary")), handler)
    assert result.content == "sync backup"


def test_every_model_dry_gives_the_customer_message():
    seen: list[str] = []
    handler = _handler({"id-primary": ProviderError(CLAUDE_DRY, 400), "id-backup": ProviderError(MINIMAX_DRY, 402), "id-last": ProviderError(FIREWORKS_DRY, 412)}, seen)
    result = asyncio.run(_middleware(["primary", "backup", "last"]).awrap_model_call(Request(Model("id-primary")), handler))
    assert seen == ["id-primary", "id-backup", "id-last"], "each model is tried once, never the same one twice"
    assert result.additional_kwargs["error_reason"] == "quota"
    assert "temporarily out of capacity" in result.content
    assert "provider account" not in result.content, "a customer cannot fix the operator's billing"


def test_a_non_quota_error_is_not_swapped():
    seen: list[str] = []
    handler = _handler({"id-primary": ProviderError("Error code: 401 - invalid api key", 401), "id-backup": "should not be used"}, seen)
    result = asyncio.run(_middleware(["primary", "backup"]).awrap_model_call(Request(Model("id-primary")), handler))
    assert seen == ["id-primary"]
    assert result.additional_kwargs["error_reason"] == "auth"


def test_no_fallbacks_configured_changes_nothing(_factory):
    seen: list[str] = []
    handler = _handler({"id-primary": ProviderError(CLAUDE_DRY, 400)}, seen)
    result = asyncio.run(_middleware([]).awrap_model_call(Request(Model("id-primary")), handler))
    assert seen == ["id-primary"] and _factory == []
    assert result.additional_kwargs["error_reason"] == "quota"


def test_an_unknown_fallback_name_is_skipped(_factory):
    seen: list[str] = []
    handler = _handler({"id-primary": ProviderError(CLAUDE_DRY, 400), "id-backup": "backup"}, seen)
    result = asyncio.run(_middleware(["no-such-model", "backup"]).awrap_model_call(Request(Model("id-primary")), handler))
    assert result.content == "backup"
