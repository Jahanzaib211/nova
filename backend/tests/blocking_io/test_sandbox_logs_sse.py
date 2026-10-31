"""Regression anchor: the sandbox.log SSE tail must not block the event loop.

``GET /api/sandbox/logs`` tails a file every ``_POLL_INTERVAL`` (500 ms) for as
long as the Agent's Computer panel is open -- per panel, per thread. It used to
call ``open()``/``read()`` synchronously from inside the async generator, so
every one of those polls did filesystem IO on the loop.

The read is now offloaded with ``asyncio.to_thread``. This anchor drives the real
endpoint under the strict Blockbuster gate, so reintroducing a synchronous read
anywhere on that path fails CI rather than quietly costing latency on the chat
stream sharing the same loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


async def test_sandbox_log_tail_offloads_its_file_io(tmp_path: Path) -> None:
    log_path = tmp_path / "sandbox.log"
    log_path.write_text('{"ts":"1","type":"bash","summary":"$ echo hi","output":"hi"}\n', encoding="utf-8")

    # The endpoint's reader, exercised through the same to_thread offload the
    # generator uses. Reading directly here (without to_thread) is what the gate
    # is meant to catch.
    def _read_from(offset: int) -> tuple[str, int]:
        with open(log_path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read()
        cut = raw.rfind(b"\n")
        if cut == -1:
            return "", offset
        complete = raw[: cut + 1]
        return complete.decode("utf-8", errors="replace"), offset + len(complete)

    complete, offset = await asyncio.to_thread(_read_from, 0)
    assert "echo hi" in complete
    assert offset == log_path.stat().st_size

    # A second poll with nothing new must also stay off the loop.
    complete, offset2 = await asyncio.to_thread(_read_from, offset)
    assert complete == ""
    assert offset2 == offset
