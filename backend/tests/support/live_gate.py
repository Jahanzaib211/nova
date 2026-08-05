"""Opt-in gate for tests that hit the real network, real LLMs, or real money.

Why this exists: `make test` was non-hermetic. The live modules skipped only on
`CI`, so GitHub Actions (which sets `CI=true`) was always green while a
developer with a `config.yaml` and the `trading` extra installed ran the exact
same command and got failures — real LLM calls that didn't emit the expected
tool call, real requests to Yahoo Finance and Binance. A regression check that
answers differently for CI and for the person running it is not a check; you
learn to ignore its red.

Live tests now require an explicit `DEERFLOW_LIVE_TESTS=1`. Everything else is
unchanged: `CI` still forces a skip (belt and braces — CI must never spend money
or depend on a third party's uptime), and the pre-existing credential/dependency
preconditions still apply on top.

    DEERFLOW_LIVE_TESTS=1 uv run pytest tests/test_market_data_live.py
"""

from __future__ import annotations

import os

LIVE_ENV_VAR = "DEERFLOW_LIVE_TESTS"

_TRUTHY = {"1", "true", "yes", "on"}


def live_tests_enabled() -> bool:
    """True only when the operator explicitly opted in and we're not in CI."""
    if os.getenv("CI", "").lower() in _TRUTHY:
        return False
    return os.getenv(LIVE_ENV_VAR, "").strip().lower() in _TRUTHY


def live_skip_reason() -> str | None:
    """``None`` when live tests should run, else a human-readable skip reason."""
    if os.getenv("CI", "").lower() in _TRUTHY:
        return "Live tests never run in CI"
    if not live_tests_enabled():
        return f"Live tests are opt-in: set {LIVE_ENV_VAR}=1 to run them (they hit real APIs and cost money)"
    return None
