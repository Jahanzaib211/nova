"""The shared optional-``redis`` import guard (four components used to copy it)."""

from __future__ import annotations

import builtins

import pytest

from deerflow.utils.redis_import import import_redis_asyncio, redis_install_hint


def test_missing_redis_names_the_component_and_the_extra(monkeypatch):
    real_import = builtins.__import__

    def _no_redis(name, *args, **kwargs):
        if name == "redis.asyncio" or name.startswith("redis"):
            raise ImportError("no redis")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_redis)
    with pytest.raises(ImportError, match=r"redis cancel signal.*deerflow-harness\[redis\]"):
        import_redis_asyncio("cancel signal")


def test_hint_keeps_the_component_specific_wording():
    assert redis_install_hint("rate limiter").startswith("redis is required for the redis rate limiter. Install the package extra with:")


@pytest.mark.parametrize(
    "module",
    ["app.gateway.rate_limiter", "deerflow.runtime.stream_bridge.redis_provider", "deerflow.runtime.runs.cancel_signal", "deerflow.runtime.runs.distributed_lock"],
)
def test_no_component_keeps_its_own_copy(module):
    import importlib

    assert not hasattr(importlib.import_module(module), "_ensure_redis_import")
