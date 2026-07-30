"""Size-guard tests for write_file_tool (issue #3189, PR #3195; auto-chunking
follow-up).

These tests verify that write_file_tool auto-chunks single-shot payloads
between the soft threshold (200 KB default) and the hard ceiling (2 MB
default) into safe internal writes, rejects payloads above the hard
ceiling with an actionable message, and leaves append-mode and env-override
paths untouched. They run purely against the tool's internal guard — no
real sandbox or filesystem is exercised, so they're fast and hermetic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deerflow.sandbox import tools as tools_module
from deerflow.sandbox.tools import _split_utf8_safe, write_file_tool


def _call_write_file(*, content: str, append: bool = False) -> tuple[str, MagicMock]:
    """Invoke write_file_tool via its underlying callable.

    We patch the sandbox initialisation chain to a no-op MagicMock so the test
    focuses purely on the size guard. The guard runs BEFORE any sandbox call,
    so when the hard ceiling rejects we never enter the patched path. Returns
    both the tool's result string and the mocked sandbox, so callers can
    assert on exactly how many internal write_file() calls happened and with
    what append flags (proof of chunking, not just a success string).
    """
    fn = getattr(write_file_tool, "func", write_file_tool)
    runtime = MagicMock()

    with (
        patch.object(tools_module, "ensure_sandbox_initialized") as mock_ensure,
        patch.object(tools_module, "ensure_thread_directories_exist"),
        patch.object(tools_module, "is_local_sandbox", return_value=False),
        patch.object(tools_module, "get_file_operation_lock") as mock_lock,
    ):
        sandbox = MagicMock()
        sandbox.write_file = MagicMock()
        mock_ensure.return_value = sandbox
        mock_lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        result = fn(
            runtime=runtime,
            description="test write",
            path="/tmp/test.txt",
            content=content,
            append=append,
        )
        return result, sandbox


def test_below_soft_cap_writes_in_a_single_call():
    """A 150 KB payload sits under the 200 KB default soft threshold and must
    pass straight through to the sandbox layer as one write, not chunked.
    """
    payload = "a" * (150 * 1024)
    result, sandbox = _call_write_file(content=payload)
    assert result == "OK"
    sandbox.write_file.assert_called_once_with("/tmp/test.txt", payload, False)


def test_above_soft_cap_auto_chunks_instead_of_erroring():
    """A 250 KB payload — over the 200 KB soft threshold, well under the 2 MB
    hard ceiling — must succeed via automatic internal chunking rather than
    erroring. The full content still lands in the file; verify every chunk
    was written (first with append=False, the rest with append=True) and
    that concatenating them reproduces the original content exactly.
    """
    payload = "abcdefghij" * (25 * 1024)  # 250 KB, non-trivial content
    result, sandbox = _call_write_file(content=payload)

    assert result.startswith("OK (auto-chunked into ")
    assert "256000 bytes total" in result

    calls = sandbox.write_file.call_args_list
    assert len(calls) > 1, "expected more than one internal write_file call for a chunked write"
    # First chunk overwrites, every subsequent chunk appends.
    assert calls[0].args[2] is False
    assert all(call.args[2] is True for call in calls[1:])
    # Every chunk targets the same path.
    assert all(call.args[0] == "/tmp/test.txt" for call in calls)
    # Concatenating the written pieces reproduces the original content exactly.
    assert "".join(call.args[1] for call in calls) == payload


def test_above_hard_ceiling_still_returns_actionable_error():
    """A payload over the 2 MB hard ceiling is rejected outright — even
    auto-chunking refuses it, since a call this large is almost certainly a
    runaway generation, not a legitimate document. The error message must
    name the ceiling, the actual size, and steer the LLM toward str_replace /
    append=True.
    """
    payload = "a" * (3 * 1024 * 1024)  # 3 MB
    result, sandbox = _call_write_file(content=payload)

    assert result.startswith("Error: write_file content")
    assert "3145728 bytes" in result
    assert "hard limit" in result
    assert "str_replace" in result, "Error must point to str_replace as the preferred incremental-edit path."
    assert "append=True" in result, "Error must also surface the append-in-chunks alternative."
    sandbox.write_file.assert_not_called()


def test_above_hard_ceiling_with_append_true_bypasses_guard():
    """append=True is the *correct* way to write a large document in chunks,
    so neither threshold applies to it — including the hard ceiling. append
    mode is the documented escape hatch precisely because each individual
    append call is expected to already be a manageable, caller-chosen chunk.
    """
    payload = "a" * (3 * 1024 * 1024)  # 3 MB — over the hard ceiling
    result, sandbox = _call_write_file(content=payload, append=True)
    assert result == "OK", f"append=True must bypass both size thresholds, got: {result!r}"
    sandbox.write_file.assert_called_once_with("/tmp/test.txt", payload, True)


def test_env_override_raises_soft_cap(monkeypatch: pytest.MonkeyPatch):
    """Setting DEERFLOW_WRITE_FILE_MAX_BYTES lets deployments accept larger
    single-shot (non-chunked) payloads when the underlying LLM/network can
    demonstrably handle them.
    """
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", str(300 * 1024))
    payload = "a" * (250 * 1024)  # 250 KB — would normally auto-chunk at 200 KB
    result, sandbox = _call_write_file(content=payload)
    assert result == "OK"
    sandbox.write_file.assert_called_once_with("/tmp/test.txt", payload, False)


def test_env_override_zero_disables_soft_cap_but_hard_ceiling_still_applies(monkeypatch: pytest.MonkeyPatch):
    """Setting the soft-cap env var to 0 is the documented escape hatch for
    operators who want to opt out of auto-chunking entirely — but the
    separate hard ceiling (a distinct env var) still guards against a
    genuinely pathological payload.
    """
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", "0")
    payload = "a" * (500 * 1024)  # 500 KB — under the 2 MB hard ceiling
    result, sandbox = _call_write_file(content=payload)
    assert result == "OK"
    sandbox.write_file.assert_called_once_with("/tmp/test.txt", payload, False)


def test_env_override_malformed_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    """A typo in the env var (e.g. 'lots') must not crash the tool — fall
    back silently to the safe 200 KB default. Crashing on every write because
    of a misconfigured env var would be far worse than ignoring it.
    """
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", "lots")
    # 100 KB is comfortably under the 200 KB fallback default, so it must
    # succeed as a single write, proving the malformed value didn't crash
    # the tool and didn't fall back to something stricter than documented.
    payload = "a" * (100 * 1024)
    result, sandbox = _call_write_file(content=payload)
    assert result == "OK"
    sandbox.write_file.assert_called_once_with("/tmp/test.txt", payload, False)


# ---------------------------------------------------------------------------
# _split_utf8_safe — the chunking primitive itself
# ---------------------------------------------------------------------------


def test_split_utf8_safe_never_breaks_a_multibyte_character():
    """A chunk boundary landing mid-character would corrupt the content on
    decode. Build a string where the natural byte-count cut point falls
    exactly inside a multi-byte emoji, and confirm every chunk round-trips
    through UTF-8 cleanly and the reassembled content is byte-identical.
    """
    # Each emoji is 4 UTF-8 bytes; a chunk size not a multiple of 4 forces
    # the boundary-backoff logic to actually engage.
    content = "🎉" * 1000  # 4000 bytes total
    chunks = _split_utf8_safe(content, max_chunk_bytes=999)  # deliberately not a multiple of 4

    assert len(chunks) > 1
    for chunk in chunks:
        # Would raise UnicodeDecodeError/EncodeError if a boundary split a
        # character in half — the fact this is already a `str` (not bytes)
        # means _split_utf8_safe itself must have decoded cleanly, but
        # re-encoding/decoding here is a second, independent confirmation.
        chunk.encode("utf-8").decode("utf-8")
    assert "".join(chunks) == content


def test_split_utf8_safe_content_under_limit_returns_single_chunk():
    content = "hello world"
    assert _split_utf8_safe(content, max_chunk_bytes=1024) == [content]
