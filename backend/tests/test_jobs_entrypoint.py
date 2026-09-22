"""The worker process entrypoint wires config → settings and refuses to start
when jobs are disabled; the demo handler is registered."""

from __future__ import annotations

import argparse

import pytest

from app.jobs.handlers import build_registry
from app.jobs.worker import serve, settings_from_config

_SANDBOX = {"use": "deerflow.sandbox.local:LocalSandboxProvider"}


@pytest.fixture(autouse=True)
def _no_global_logging_side_effects(monkeypatch):
    """serve() applies config.log_level to the process loggers; leaving that
    on would change caplog-based tests that run after this module."""
    import app.jobs.worker as w

    monkeypatch.setattr(w, "apply_logging_level", lambda _level: None)


def test_registry_has_the_demo_handler():
    assert "jobs.demo.sleep" in build_registry().types()


def test_settings_follow_config_and_cli_overrides(monkeypatch):
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig.model_validate({"sandbox": _SANDBOX, "jobs": {"enabled": True, "queues": ["default", "email"], "concurrency": 2, "lease_ttl_seconds": 45}})
    import app.jobs.worker as w

    monkeypatch.setattr(w, "get_app_config", lambda: cfg)
    s = settings_from_config(argparse.Namespace(queues=None, concurrency=None, worker_id="w1", force=False))
    assert s.queues == ["default", "email"] and s.concurrency == 2 and s.lease_ttl.total_seconds() == 45
    s = settings_from_config(argparse.Namespace(queues="agents", concurrency=8, worker_id=None, force=False))
    assert s.queues == ["agents"] and s.concurrency == 8 and s.worker_id


@pytest.mark.anyio
async def test_serve_refuses_when_disabled(monkeypatch):
    import app.jobs.worker as w
    from deerflow.config.app_config import AppConfig

    monkeypatch.setattr(w, "get_app_config", lambda: AppConfig.model_validate({"sandbox": _SANDBOX}))
    rc = await serve(argparse.Namespace(queues=None, concurrency=None, worker_id=None, force=False))
    assert rc == 2


@pytest.mark.anyio
async def test_serve_initialises_the_engine_from_the_database_section(monkeypatch, tmp_path):
    """Regression: the first live start passed the whole AppConfig to
    init_engine_from_config, which wants the ``database`` sub-config."""
    import app.jobs.worker as w
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig.model_validate({"sandbox": _SANDBOX, "jobs": {"enabled": True}, "database": {"backend": "sqlite", "sqlite_dir": str(tmp_path)}})
    monkeypatch.setattr(w, "get_app_config", lambda: cfg)
    seen = {}

    async def fake_init(database_cfg):
        seen["backend"] = database_cfg.backend

    monkeypatch.setattr(w, "init_engine_from_config", fake_init)

    class _SF:  # any non-None session factory
        pass

    monkeypatch.setattr(w, "get_session_factory", lambda: _SF())

    async def fake_serve(self):
        return None

    monkeypatch.setattr(w.Worker, "serve", fake_serve)

    async def fake_close():
        return None

    monkeypatch.setattr(w, "close_engine", fake_close)
    rc = await serve(argparse.Namespace(queues=None, concurrency=None, worker_id="w", force=False))
    assert rc == 0 and seen == {"backend": "sqlite"}
