"""The Terminal's "N cmds" counter must count commands, not output chunks.

`_write_sandbox_observation` decides `is_command_line` and increments
terminal_stats.json. Streaming writes every delta/replace frame with the *same*
obs_id and state="running" (see `on_chunk` in `_bash_streaming`), so the
opening-frame branch matched each chunk too: one streamed command emitting 50
chunks added 51 to a number the UI labels "N cmds" and renders without the "~"
prefix, i.e. as exact.

It was invisible because the WS emit is nested under `delta is None and replace
is None` -- the inflated value sat on disk and only surfaced as a jump when the
next real command pushed a frame.
"""


def _count_calls(calls):
    """Replay a frame sequence through the real predicate."""
    total = 0
    for obs_id, state, delta, replace, tool in calls:
        is_command_line = delta is None and replace is None and ((obs_id is not None and state == "running") or (tool == "bash" and obs_id is None))
        total += 1 if is_command_line else 0
    return total


def test_streamed_command_counts_once_not_per_chunk():
    """One opening frame + 50 deltas + one closing frame == 1 command."""
    calls = [("id1", "running", None, None, "bash")]
    calls += [("id1", "running", f"chunk{i}", None, "bash") for i in range(50)]
    calls += [("id1", "done", None, None, "bash")]
    assert _count_calls(calls) == 1


def test_replace_frames_do_not_count():
    calls = [("id1", "running", None, None, "bash"), ("id1", "running", None, "whole body", "bash")]
    assert _count_calls(calls) == 1


def test_non_streamed_bash_counts_once():
    assert _count_calls([(None, None, None, None, "bash")]) == 1


def test_two_streamed_commands_count_two():
    calls = [
        ("id1", "running", None, None, "bash"),
        ("id1", "running", "out", None, "bash"),
        ("id1", "done", None, None, "bash"),
        ("id2", "running", None, None, "bash"),
        ("id2", "running", "out", None, "bash"),
        ("id2", "done", None, None, "bash"),
    ]
    assert _count_calls(calls) == 2


def test_non_bash_tools_do_not_count():
    assert _count_calls([(None, None, None, None, "read_file")]) == 0


def test_predicate_matches_the_shipped_source():
    """Guard the predicate duplicated above against drift in tools.py.

    Read as text rather than imported: deerflow.sandbox.tools participates in an
    import cycle (workspace_tools imports back into it), so pulling it in from a
    test can fail at collection depending on import order. The assertion is a
    source-level contract, so text is the right granularity anyway.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "packages/harness/deerflow/sandbox/tools.py"
    text = src.read_text()
    expected = 'is_command_line = delta is None and replace is None and ((obs_id is not None and state == "running") or (tool == "bash" and obs_id is None))'
    assert expected in text, "is_command_line changed in tools.py -- update _count_calls in this test to match"
