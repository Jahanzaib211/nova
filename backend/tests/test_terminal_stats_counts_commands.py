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


import deerflow.tools.builtins.workspace_tools  # noqa: F401  isort:skip
from deerflow.sandbox.tools import is_command_line  # noqa: E402  isort:skip


def _count_calls(calls):
    """Replay a frame sequence through the shipped predicate.

    This used to re-implement the predicate inline, so the test could pass
    while the real one was wrong -- the same "tests a stub" trap that hid the
    skill tool-policy bug. It now imports the single definition.
    """
    total = 0
    for obs_id, state, delta, replace, tool in calls:
        record = {"type": tool}
        if obs_id is not None:
            record["id"] = obs_id
        if state is not None:
            record["state"] = state
        if delta is not None:
            record["delta"] = delta
        if replace is not None:
            record["replace"] = replace
        total += 1 if is_command_line(record) else 0
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


def test_the_predicate_is_not_duplicated_anywhere():
    """The definition of "a command" must exist exactly once.

    This file used to re-implement the predicate and guard it against drift by
    asserting the shipped source *as text*. That is a weaker contract than
    simply importing it, which is what `_count_calls` now does -- so the drift
    guard is replaced by the thing it was approximating: no second copy of the
    expression may exist in the tree.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # Assembled at runtime so this file does not contain the literal it greps
    # for -- otherwise the test matches its own source and always fails.
    needle = '(tool == "bash"' + " and obs_id is None)"
    hits = subprocess.run(
        ["grep", "-rn", needle, str(root / "packages"), str(root / "app"), str(root / "tests")],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert hits == "", f"the command predicate was re-implemented instead of imported:\n{hits}"
