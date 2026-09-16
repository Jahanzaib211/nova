"""Multi-line commands must never reach the sandbox with a newline in them.

The upstream sandbox server mis-parses a newline in ``exec_command`` and returns
an ``ErrorObservation`` whose ``exit_code`` it then fails to read.  Measured
against the live container before the fix: **10/10 multi-line commands failed,
0/10 single-line**.  In one real run 13 of 25 multi-line commands were lost.
"""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture()
def sandbox():
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        return AioSandbox(id="test-sandbox", base_url="http://localhost:8080")


def _record(sandbox):
    """Capture what actually goes on the wire; echo the payload back."""
    seen = []

    def exec_command(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(data=SimpleNamespace(output="ok"))

    sandbox._client.shell.exec_command = exec_command
    return seen


class TestSingleLineWrapper:
    def test_single_line_command_is_untouched(self, sandbox):
        """107 single-line commands ran clean in the live log; don't disturb them."""
        seen = _record(sandbox)
        sandbox.execute_command("echo hello && ls -la /tmp")
        assert seen == ["echo hello && ls -la /tmp"]

    @pytest.mark.parametrize(
        "command",
        [
            "echo AAA\necho BBB",
            'for i in 1 2 3; do\n  echo "n=$i"\ndone',
            "cat <<EOF\nhello\nworld\nEOF",
            'X=$(echo hi)\necho "got: $X"',
            'printf "a\tb\n"\necho done',
        ],
        ids=["simple", "for-loop", "heredoc", "subshell", "escapes"],
    )
    def test_multiline_never_puts_a_newline_on_the_wire(self, sandbox, command):
        seen = _record(sandbox)
        sandbox.execute_command(command)
        assert len(seen) == 1
        assert "\n" not in seen[0], "a newline reached exec_command; the server mis-parses it"

    @pytest.mark.parametrize(
        "command",
        [
            "echo AAA\necho BBB",
            'for i in 1 2 3; do\n  echo "n=$i"\ndone',
            "cat <<EOF\nhello\nworld\nEOF",
            'echo "the agent\'s host process!"\necho second',
            'printf "a\tb\n"\necho done',
        ],
        ids=["simple", "for-loop", "heredoc", "bang-in-quotes", "escapes"],
    )
    def test_payload_round_trips_byte_exact(self, sandbox, command):
        """Base64 must preserve heredocs, quoting, tabs and backslashes exactly."""
        seen = _record(sandbox)
        sandbox.execute_command(command)
        encoded = seen[0].split("echo ", 1)[1].split(" |", 1)[0]
        assert base64.b64decode(encoded).decode("utf-8") == command

    def test_wrapped_form_runs_non_interactive_bash(self, sandbox):
        """`bash -s` disables history expansion, killing `bash: !: event not found`."""
        seen = _record(sandbox)
        sandbox.execute_command('echo "hi!"\necho again')
        assert seen[0].endswith("| base64 -d | bash -s")


class TestErrorObservationRetryStillWorks:
    def test_retry_also_sends_the_wrapped_form(self, sandbox):
        """The signature retry is a backstop; it must not re-send a raw newline."""
        from deerflow.community.aio_sandbox.aio_sandbox import _ERROR_OBSERVATION_SIGNATURE

        seen = []
        outputs = [f"Command failed: {_ERROR_OBSERVATION_SIGNATURE}", "recovered"]

        def exec_command(command, **kwargs):
            seen.append(command)
            return SimpleNamespace(data=SimpleNamespace(output=outputs[len(seen) - 1]))

        sandbox._client.shell.exec_command = exec_command
        result = sandbox.execute_command("echo one\necho two")

        assert result == "recovered"
        assert len(seen) == 2, "the retry should have fired"
        assert all("\n" not in c for c in seen)
