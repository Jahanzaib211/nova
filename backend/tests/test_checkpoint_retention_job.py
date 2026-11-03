"""The retention job and CLI are off and dry by default."""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

import pytest

from app.jobs import checkpoint_retention as cli
from app.jobs.handlers import build_registry, maintenance
from nova.config.checkpoint_retention_config import CheckpointRetentionConfig


class _Ctx:
    def __init__(self, payload=None):
        self.payload = payload or {}

    async def heartbeat(self):
        return None


def _patch(monkeypatch, cfg, engine=object()):
    monkeypatch.setattr(maintenance, "get_app_config", lambda: SimpleNamespace(checkpoint_retention=cfg))
    monkeypatch.setattr(maintenance, "get_engine", lambda: engine)
    calls = []

    async def fake_prune(engine, policy, *, dry_run):
        calls.append(dry_run)
        return SimpleNamespace(as_dict=lambda: {"dry_run": dry_run})

    monkeypatch.setattr(maintenance, "prune_checkpoints", fake_prune)
    return calls


def test_defaults_are_conservative():
    cfg = CheckpointRetentionConfig()
    assert cfg.enabled is False
    assert cfg.dry_run is True
    assert cfg.keep_per_thread >= 1


def test_handler_is_registered():
    assert maintenance.CHECKPOINT_PRUNE_JOB in build_registry()._handlers


def test_disabled_job_does_nothing(monkeypatch):
    calls = _patch(monkeypatch, CheckpointRetentionConfig())
    out = asyncio.run(maintenance.checkpoint_prune(_Ctx()))
    assert "skipped" in out
    assert calls == []


def test_enabled_job_stays_dry_until_dry_run_is_off(monkeypatch):
    calls = _patch(monkeypatch, CheckpointRetentionConfig(enabled=True))
    asyncio.run(maintenance.checkpoint_prune(_Ctx()))
    assert calls == [True]

    calls = _patch(monkeypatch, CheckpointRetentionConfig(enabled=True, dry_run=False))
    asyncio.run(maintenance.checkpoint_prune(_Ctx()))
    assert calls == [False]


def test_payload_can_force_a_dry_run_but_not_undo_one(monkeypatch):
    calls = _patch(monkeypatch, CheckpointRetentionConfig(enabled=True, dry_run=False))
    asyncio.run(maintenance.checkpoint_prune(_Ctx({"dry_run": True})))
    assert calls == [True]

    calls = _patch(monkeypatch, CheckpointRetentionConfig(enabled=True, dry_run=True))
    asyncio.run(maintenance.checkpoint_prune(_Ctx({"dry_run": False})))
    assert calls == [True]


def test_memory_backend_is_skipped(monkeypatch):
    calls = _patch(monkeypatch, CheckpointRetentionConfig(enabled=True, dry_run=False), engine=None)
    assert "skipped" in asyncio.run(maintenance.checkpoint_prune(_Ctx()))
    assert calls == []


def test_cli_apply_refused_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_app_config", lambda: SimpleNamespace(checkpoint_retention=CheckpointRetentionConfig(), database=None))
    args = argparse.Namespace(apply=True, keep_per_thread=None, keep_active_days=None, batch_size=None, max_batches=None)
    assert asyncio.run(cli.run(args)) == 2
    assert "refused" in capsys.readouterr().err


def test_cli_flags_override_the_policy():
    args = argparse.Namespace(apply=False, keep_per_thread=5, keep_active_days=0.0, batch_size=None, max_batches=None)
    policy = cli.build_policy(args, CheckpointRetentionConfig())
    assert policy.keep_per_thread == 5
    assert policy.keep_active_days == 0.0
    assert policy.batch_size == CheckpointRetentionConfig().batch_size

    with pytest.raises(ValueError):
        cli.build_policy(argparse.Namespace(apply=False, keep_per_thread=0, keep_active_days=None, batch_size=None, max_batches=None), CheckpointRetentionConfig())
