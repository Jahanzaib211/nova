"""A dead ACP adapter must fail the turn in seconds, not after the turn budget.

The ``acp`` library's receive loop stops on EOF without failing the pending
request, so ``initialize`` waited for the outer timeout — 30 minutes in
production (2026-10-29) for an adapter that died 8 seconds in with
``claude-agent-acp: not found``. The transport now races each request against
the process exiting.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.acp_transport import ACPAdapterExited, run_acp_prompt
from deerflow.runtimes.types import policy_for_mode


def _run(cfg: ACPAgentConfig, tmp_path) -> None:
    asyncio.run(
        run_acp_prompt(
            cfg,
            "hello",
            cwd=str(tmp_path),
            mcp_servers=[],
            permission=policy_for_mode("plan"),
            on_text=lambda _sid, _t: None,
            on_status=lambda _sid, _t: None,
            timeout=60,
        )
    )


def test_adapter_that_exits_immediately_fails_fast_with_its_stderr(tmp_path):
    cfg = ACPAgentConfig(command="sh", args=["-c", "echo 'claude-agent-acp: not found' >&2; exit 127"], description="dead")
    started = time.monotonic()
    with pytest.raises(ACPAdapterExited) as exc:
        _run(cfg, tmp_path)
    assert time.monotonic() - started < 10, "must not wait for the turn budget"
    assert "127" in str(exc.value)
    assert "not found" in str(exc.value)
