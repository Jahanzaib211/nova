"""One "Check model" probe for ``POST /api/models/{name}/test`` and
``models.probe``.

The capability had no timeout (a stopped local server held the call open)
and attached tracing, so every probe became a Langfuse/LangSmith trace.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deerflow.models import probe as probe_module
from deerflow.models.probe import PROBE_PROMPT, probe_model


class _Config:
    def __init__(self, names):
        self.names = set(names)

    def get_model_config(self, name):
        return SimpleNamespace(name=name) if name in self.names else None


def _fake_factory(monkeypatch, reply):
    calls = []

    class _Model:
        async def ainvoke(self, prompt):
            calls.append(prompt)
            if isinstance(reply, BaseException):
                raise reply
            if reply == "hang":
                await asyncio.sleep(10)
            return SimpleNamespace(content=reply)

    def _create(name, *, app_config=None, attach_tracing=True, **kw):
        calls.append({"attach_tracing": attach_tracing})
        return _Model()

    monkeypatch.setattr("deerflow.models.factory.create_chat_model", _create)
    return calls


def test_probe_disables_tracing_and_reports_the_reply(monkeypatch):
    calls = _fake_factory(monkeypatch, "ok")
    result = asyncio.run(probe_model("m", app_config=_Config(["m"])))
    assert result.ok and result.detail == "ok" and not result.timed_out
    assert calls == [{"attach_tracing": False}, PROBE_PROMPT]


def test_probe_times_out(monkeypatch):
    _fake_factory(monkeypatch, "hang")
    result = asyncio.run(probe_model("m", app_config=_Config(["m"]), timeout=0.05))
    assert result.ok is False and result.timed_out and "Timed out" in result.detail


def test_probe_reports_errors_and_unknown_models(monkeypatch):
    _fake_factory(monkeypatch, RuntimeError("bad key"))
    result = asyncio.run(probe_model("m", app_config=_Config(["m"])))
    assert (result.ok, result.detail) == (False, "RuntimeError: bad key")
    with pytest.raises(LookupError):
        asyncio.run(probe_model("ghost", app_config=_Config(["m"])))


def test_capability_and_router_share_the_probe(monkeypatch):
    from app.gateway.routers import models as models_router
    from deerflow.capabilities.modules import models as models_module
    from deerflow.capabilities.types import OpContext

    seen = []

    async def _probe(name, *, app_config=None, timeout=probe_module.PROBE_TIMEOUT_SECONDS):
        seen.append((name, timeout))
        if name == "ghost":
            raise LookupError(name)
        return probe_module.ModelProbeResult(ok=True, latency_ms=3, detail="ok")

    monkeypatch.setattr(probe_module, "probe_model", _probe)
    out = asyncio.run(models_module._probe(OpContext(user_id="u"), models_module.ProbeIn(name="m")))
    assert (out.ok, out.latency_ms, out.detail) == (True, 3, "ok")
    res = asyncio.run(models_router.test_model("m", config=_Config(["m"])))
    assert res.ok and res.message == "Model responded: ok"
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(models_router.test_model("ghost", config=_Config([])))
    assert exc.value.status_code == 404
    assert seen == [("m", probe_module.PROBE_TIMEOUT_SECONDS), ("m", models_router.MODEL_TEST_TIMEOUT_SEC), ("ghost", models_router.MODEL_TEST_TIMEOUT_SEC)]
