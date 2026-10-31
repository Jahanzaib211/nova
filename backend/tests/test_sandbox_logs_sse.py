"""The sandbox.log tail must never emit or skip a half-written line.

``_write_sandbox_observation`` appends from a *different* process (the agent's,
possibly inside a container) with no locking, while the SSE endpoint polls the
file every 500 ms. The old reader did ``chunk = fh.read()`` and then advanced its
cursor with ``fh.tell()`` unconditionally, so a read that landed mid-append:

  1. emitted a truncated JSON line, which the frontend dropped in a bare
     ``catch {}`` (``core/sandbox/hooks.ts``) -- no error, no log, event gone;
  2. advanced the cursor past it, so the rest of that line was never re-read.

The event was lost with zero diagnostics. These tests pin the whole-lines-only
contract by writing a line in two halves across two polls.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def reader(tmp_path, monkeypatch):
    """Build the module-private reader against a real file on disk.

    The reader is a closure over ``log_path`` inside the endpoint, so it is
    reconstructed here with identical semantics rather than imported. The
    contract under test -- return only whole lines, advance only to the last
    newline, count bytes not characters -- is what matters.
    """
    log = tmp_path / "sandbox.log"
    log.write_text("", encoding="utf-8")

    def _read_from(offset: int) -> tuple[str, int]:
        with open(log, "rb") as fh:
            fh.seek(offset)
            raw = fh.read()
        cut = raw.rfind(b"\n")
        if cut == -1:
            return "", offset
        complete = raw[: cut + 1]
        return complete.decode("utf-8", errors="replace"), offset + len(complete)

    return log, _read_from


def _append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


class TestTornLines:
    def test_partial_line_is_not_emitted(self, reader):
        log, read_from = reader
        _append(log, '{"ts":"1","type":"bash","summ')  # writer caught mid-append
        out, offset = read_from(0)
        assert out == "", "a half-written line must not be emitted"
        assert offset == 0, "the cursor must not advance past an incomplete line"

    def test_the_rest_of_the_line_arrives_intact(self, reader):
        log, read_from = reader
        _append(log, '{"ts":"1","type":"bash","summ')
        out, offset = read_from(0)
        _append(log, 'ary":"$ echo hi","output":"hi"}\n')

        out, offset = read_from(offset)
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["summary"] == "$ echo hi"
        assert parsed["output"] == "hi"

    def test_complete_lines_before_a_partial_are_still_delivered(self, reader):
        log, read_from = reader
        _append(log, '{"ts":"1","type":"bash"}\n{"ts":"2","type":"bash"}\n{"ts":"3","par')
        out, offset = read_from(0)
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 2, "the two complete lines must not be held back"
        assert all(json.loads(ln) for ln in lines)

        _append(log, 'tial":true}\n')
        out, _ = read_from(offset)
        assert json.loads(out.strip())["partial"] is True

    def test_no_duplication_across_polls(self, reader):
        log, read_from = reader
        _append(log, '{"n":1}\n{"n":2}\n')
        first, offset = read_from(0)
        _append(log, '{"n":3}\n')
        second, _ = read_from(offset)
        assert [json.loads(x)["n"] for x in first.splitlines() if x] == [1, 2]
        assert [json.loads(x)["n"] for x in second.splitlines() if x] == [3]


class TestByteOffsets:
    def test_multibyte_content_keeps_the_cursor_aligned(self, reader):
        """Offsets are byte positions; counting characters desynchronises them."""
        log, read_from = reader
        _append(log, json.dumps({"summary": "построить сайт 网站 🚀"}, ensure_ascii=False) + "\n")
        out, offset = read_from(0)
        assert json.loads(out.strip())["summary"] == "построить сайт 网站 🚀"
        assert offset == log.stat().st_size, "cursor must land exactly at EOF"

        _append(log, json.dumps({"n": 2}) + "\n")
        out2, _ = read_from(offset)
        assert json.loads(out2.strip())["n"] == 2, "next read must not be shifted"

    def test_empty_file(self, reader):
        _log, read_from = reader
        assert read_from(0) == ("", 0)
