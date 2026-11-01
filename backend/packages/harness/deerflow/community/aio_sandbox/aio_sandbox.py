import base64
import errno
import logging
import shlex
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from agent_sandbox import Sandbox as AioSandboxClient

from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.exceptions import SandboxError
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.search import GrepMatch, path_matches, should_ignore_path, truncate_line

logger = logging.getLogger(__name__)

_MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

_ERROR_OBSERVATION_SIGNATURE = "'ErrorObservation' object has no attribute 'exit_code'"


def _is_set(event: Any) -> bool:
    """Whether a cancellation signal has been raised.

    Accepts an ``asyncio.Event`` (the run's abort event), a
    ``threading.Event``, or a plain callable, because the check happens on a
    worker thread inside the streaming loop and must never raise: a broken
    cancel check must not turn into a failed command.
    """
    try:
        if event is None:
            return False
        is_set = getattr(event, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        if callable(event):
            return bool(event())
    except Exception:
        return False
    return False


def _is_transport_error(exc: BaseException) -> bool:
    """Whether ``exc`` means the sandbox's HTTP endpoint could not be reached.

    Decided by exception type only -- never by message text. A command whose
    own output says "Connection refused" (``curl`` to a dead port) ran in a
    perfectly healthy sandbox; matching on text reported it as unreachable
    and threw its output away. The ``__cause__``/``__context__`` chain is
    walked because the SDK may wrap the transport error.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionRefusedError, httpx.ConnectError)):
            return True
        if isinstance(current, OSError) and current.errno == errno.ECONNREFUSED:
            return True
        current = current.__cause__ or current.__context__
    return False


def _single_line(command: str) -> str:
    """Collapse a multi-line command into one line the sandbox can parse.

    The upstream sandbox server mis-handles a newline in ``exec_command`` and
    answers with an ``ErrorObservation`` whose ``exit_code`` it then fails to
    read, surfacing to the agent as::

        Command failed: 'ErrorObservation' object has no attribute 'exit_code'

    Measured against the live container: **10/10 multi-line commands failed,
    0/10 single-line**. In one real run 13 of 25 multi-line commands were lost
    this way -- the agent silently gave up half its shell scripts.

    Base64 keeps the payload byte-exact, so heredocs, embedded quotes, tabs and
    backslashes all survive. It also runs the script through a *non-interactive*
    ``bash``, which disables history expansion -- the same run shows a bare
    ``bash: !: event not found`` for a ``!`` inside double quotes.

    Single-line commands are returned untouched: they already work, and 107 of
    them ran clean in that same log. ``bash -s`` costs nothing here because the
    default session persists neither cwd nor environment between calls (verified
    directly: ``cd /tmp`` then ``pwd`` returns the home directory either way).
    """
    if "\n" not in command:
        return command
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    return f"echo {encoded} | base64 -d | bash -s"


class AioSandbox(Sandbox):
    """Sandbox implementation using the agent-infra/sandbox Docker container.

    This sandbox connects to a running AIO sandbox container via HTTP API.
    A threading lock serializes shell commands to prevent concurrent requests
    from corrupting the container's single persistent session (see #1433).
    """

    def __init__(
        self,
        id: str,
        base_url: str,
        home_dir: str | None = None,
        busy_tracker: "Callable[[str, int], None] | None" = None,
    ):
        """Initialize the AIO sandbox.

        Args:
            id: Unique identifier for this sandbox instance.
            base_url: URL of the sandbox API (e.g., http://localhost:8080).
            home_dir: Home directory inside the sandbox. If None, will be fetched from the sandbox.
            busy_tracker: Called with ``(sandbox_id, +1)`` when a command starts
                and ``(sandbox_id, -1)`` when it finishes, so the provider's idle
                reaper can tell "nothing has touched this sandbox in an hour"
                apart from "a build has been running in it for an hour". Without
                it a long command is invisible to the reaper: the tool call
                blocks inside ``execute_command`` and refreshes nothing, so a
                build outlasting ``idle_timeout`` gets its own sandbox destroyed
                mid-run unless someone happens to have the panel open.
        """
        super().__init__(id)
        self._base_url = base_url
        self._client = AioSandboxClient(base_url=base_url, timeout=600)
        self._home_dir = home_dir
        self._lock = threading.Lock()
        self._closed = False
        self._busy_tracker = busy_tracker
        # Set by the provider so a destroyed/unreachable sandbox can be dropped
        # from tracking (and its thread mapping cleared) instead of being retried
        # forever. Never fatal if unset.
        self._on_unreachable: Callable[[str], None] | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_client(self):
        """The SDK client, or a typed error explaining that it is gone.

        :meth:`close` drops the reference so a later call fails loudly rather
        than reusing a half-closed client. It did fail -- as
        ``AttributeError: 'NoneType' object has no attribute 'file'``, which
        says nothing about what happened. Downstream that forced
        ``dev_server.py`` to recognise a released sandbox by **string-matching**
        the words "has no attribute" in a returned error message; rewording the
        exception would silently break the preview log tail.
        """
        client = self._client
        if client is None:
            raise SandboxError(f"sandbox {self.id} has been released; its client is closed")
        return client

    def close(self) -> None:
        """Best-effort close of the host-side HTTP client owned by this sandbox.

        The agent_sandbox SDK is Fern-generated and exposes no ``close()`` /
        ``__exit__``, so we reach the socket-owning ``httpx.Client`` explicitly
        through its attribute chain::

            Sandbox._client_wrapper        -> SyncClientWrapper
                .httpx_client              -> Fern HttpClient (a wrapper, NOT httpx.Client)
                    .httpx_client          -> httpx.Client     <- the real socket owner

        Closing it releases pooled sockets so long-running provider lifecycles
        do not accumulate unreclaimed host-side resources (#2872).

        Resolution is most-specific-first with graceful degradation: if a future
        SDK adds a top-level ``Sandbox.close()`` it is picked up automatically
        without changing this code. Idempotent, thread-safe, and non-fatal:
        failures during teardown are logged and swallowed so provider/backend
        cleanup is never blocked.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            # Drop the reference under the lock for use-after-close safety: any
            # later command on this instance fails loudly instead of reusing a
            # half-closed client.
            self._client = None

        if client is None:
            return

        # Walk from the real httpx.Client up to the top-level client, picking the
        # first object that actually exposes close().
        wrapper = getattr(client, "_client_wrapper", None)
        fern_http = getattr(wrapper, "httpx_client", None)
        real_httpx = getattr(fern_http, "httpx_client", None)
        target = next(
            (c for c in (real_httpx, fern_http, client) if c is not None and hasattr(c, "close")),
            None,
        )
        if target is None:
            logger.debug("AioSandbox %s: no closable client found, nothing to release", self.id)
            return

        try:
            target.close()
        except Exception as e:
            logger.warning(f"Error closing AioSandbox client for {self.id}: {e}")

    @property
    def home_dir(self) -> str:
        """Get the home directory inside the sandbox."""
        if self._home_dir is None:
            context = self._require_client().sandbox.get_context()
            self._home_dir = context.home_dir
        return self._home_dir

    # Default no_change_timeout for exec_command (seconds).  Matches the
    # client-level timeout so that long-running commands which produce no
    # output are not prematurely terminated by the sandbox's built-in 120 s
    # default.
    _DEFAULT_NO_CHANGE_TIMEOUT = 600

    def execute_command(self, command: str) -> str:
        """Execute a shell command in the sandbox.

        Uses a lock to serialize concurrent requests. The AIO sandbox
        container maintains a single persistent shell session that
        corrupts when hit with concurrent exec_command calls (returns
        ``ErrorObservation`` instead of real output). If corruption is
        detected despite the lock (e.g. multiple processes sharing a
        sandbox), the command is retried on a fresh session.

        Args:
            command: The command to execute.

        Returns:
            The output of the command.
        """
        # Held for the whole call, not pulsed at the start: a build can run for
        # hours inside this one blocking request, and the idle reaper measures
        # elapsed time, not intent.
        self._mark_busy(1)
        try:
            return self._execute_command_locked(command)
        finally:
            self._mark_busy(-1)

    def _mark_busy(self, delta: int) -> None:
        """Tell the provider a command is in flight. Never fatal."""
        if self._busy_tracker is None:
            return
        try:
            self._busy_tracker(self.id, delta)
        except Exception:  # noqa: BLE001 - bookkeeping must not break execution
            logger.debug("busy tracker failed for sandbox %s", self.id, exc_info=True)

    def _execute_command_locked(self, command: str) -> str:
        try:
            return self._execute_command_raising(command)
        except Exception as e:
            logger.error(f"Failed to execute command in sandbox: {e}")
            return f"Error: {e}"

    def _execute_command_raising(self, command: str) -> str:
        """The blocking exec, letting exceptions escape.

        :meth:`_execute_command_locked` folds every failure into an
        ``"Error: ..."`` string, which leaves a caller only the message text
        to tell a dead sandbox from a failing command. The streaming fallback
        needs the exception itself to make that call by type.
        """
        with self._lock:
            wire = _single_line(command)
            result = self._require_client().shell.exec_command(command=wire, no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
            output = result.data.output if result.data else ""

            if output and _ERROR_OBSERVATION_SIGNATURE in output:
                logger.warning("ErrorObservation detected in sandbox output, retrying on a fresh session")
                # exec_command only auto-creates a session when called with
                # no id, so the recovery session must be created explicitly
                # before we target it on retry.
                fresh_id = str(uuid.uuid4())
                self._require_client().shell.create_session(id=fresh_id)
                try:
                    result = self._require_client().shell.exec_command(command=wire, id=fresh_id, no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                    output = result.data.output if result.data else ""
                finally:
                    # Release the one-shot recovery session, best-effort, so
                    # repeated corruption can't accumulate sessions.
                    try:
                        self._require_client().shell.cleanup_session(fresh_id)
                    except Exception as cleanup_error:
                        logger.warning(f"Failed to release recovery session {fresh_id}: {cleanup_error}")

            return output if output else "(no output)"

    # This backend can report output while a command is still running, so the
    # Terminal fills line by line instead of showing one block at exit.
    supports_streaming = True

    #: How often to re-read the command's output file. 400 ms is below the
    #: 500 ms at which the gateway tails sandbox.log, so the log is never the
    #: slower half of the pipe.
    _STREAM_POLL_INTERVAL = 0.4

    #: Stop tailing once the output passes this size and just wait for the exit.
    #: The file API returns whole files, so tailing a runaway log would re-read
    #: it on every poll -- quadratic in bytes for output nobody can read anyway.
    #: The full output is still returned; only the live narration stops.
    _STREAM_MAX_TAIL_BYTES = 1_000_000

    def execute_command_streaming(
        self,
        command: str,
        on_chunk: "Callable[[str, bool], None]",
        cancel_event: "Any | None" = None,
    ) -> str:
        """Run ``command``, reporting output as it appears rather than at exit.

        ``on_chunk(text, replace)`` is called for each change in output:
        ``replace=False`` appends ``text``, ``replace=True`` swaps the whole
        body. Returns the complete output, exactly as :meth:`execute_command`.

        **Why this redirects to a file rather than polling ``shell.view``.**
        The obvious design -- ``exec_command(async_mode=True)`` then poll
        ``view`` -- does not work against this sandbox server. ``exec_command``
        does return immediately, but ``view`` reports ``output=''`` and an empty
        ``console`` for the entire run and only produces the text at
        ``status='completed'``. Measured against the live container on a command
        printing one line a second for four seconds::

            t+0.01s status='running'   output=''
            t+2.11s status='running'   output=''
            t+3.52s status='running'   output=''
            t+4.22s status='completed' output='line 1\nline 2\nline 3\nline 4'

        Driving the same command through an interactive PTY
        (``write_to_process``) buffers identically, so the shell API simply does
        not expose partial output.

        The file API does. Redirecting to a file and polling ``file.read_file``
        yields the output as it is written::

            t+0.04s status='running'   file='line 1\n'
            t+1.25s status='running'   file='line 1\nline 2\n'
            t+2.46s status='running'   file='line 1\nline 2\nline 3\n'

        It also avoids the shell entirely while the command runs, so the poll
        cannot corrupt the session -- the failure mode that cost 13 of 25
        multi-line commands before the base64 wrapper.
        """
        self._mark_busy(1)
        try:
            return self._execute_command_streaming_locked(command, on_chunk, cancel_event=cancel_event)
        finally:
            self._mark_busy(-1)

    @staticmethod
    def _stream_wire(command: str, log_path: str) -> str:
        """The command, byte-exact, with all output redirected to ``log_path``.

        Always base64 -- even for a single-line command that
        :func:`_single_line` would pass through untouched. Appending
        ``> file 2>&1`` to raw text binds the redirect to the *last* command
        only, so ``echo a; echo b`` would stream half its output and return the
        other half. Wrapping the whole script in one ``bash -s`` makes the
        redirect unambiguous, and keeps the newline off the wire as a bonus.
        """
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        return f"echo {encoded} | base64 -d | bash -s > {shlex.quote(log_path)} 2>&1"

    def _execute_command_streaming_locked(
        self,
        command: str,
        on_chunk: "Callable[[str, bool], None]",
        cancel_event: "Any | None" = None,
    ) -> str:
        # Every blocking fallback runs AFTER the ``with`` block exits. The
        # blocking path takes the same non-reentrant lock (calling it while
        # holding this one deadlocked the worker thread on 2026-11-01), and
        # releasing/re-acquiring by hand inside the ``with`` both let another
        # command slip in mid-call and masked the real error with a
        # RuntimeError whenever the release itself failed.
        fallback: str | None = None
        with self._lock:
            # A dedicated session per command: the default session is shared
            # with `shell_session`, whose screen would mix another process's
            # output into this command's stream.
            session_id = f"nova-stream-{uuid.uuid4()}"
            log_path = f"/tmp/nova-stream-{uuid.uuid4().hex}.log"

            try:
                self._require_client().shell.create_session(id=session_id)
                self._require_client().shell.exec_command(
                    command=self._stream_wire(command, log_path),
                    id=session_id,
                    async_mode=True,
                    no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("streaming exec failed to start (%s); falling back to blocking exec", e)
                self._cleanup_stream_session(session_id)
                fallback = "start"

            if fallback is None:
                seen, cancelled = self._stream_until_done(session_id, log_path, on_chunk, cancel_event)
                if cancelled:
                    # Say so plainly: the agent must not read a partial output
                    # as a finished command and build on it.
                    return f"{seen}\n[command cancelled by the run's stop signal]".strip()
                # The corruption backstop still applies: if the wrapper somehow
                # did not prevent it, re-run through the blocking path, which
                # retries on a fresh session.
                if seen and _ERROR_OBSERVATION_SIGNATURE in seen:
                    logger.warning("ErrorObservation in streamed output, retrying on the blocking path")
                    fallback = "error_observation"
                else:
                    return seen if seen else "(no output)"

        # Lock released.
        if fallback == "start":
            return self._blocking_fallback(command)
        return self._execute_command_locked(command)

    def _stream_until_done(
        self,
        session_id: str,
        log_path: str,
        on_chunk: "Callable[[str, bool], None]",
        cancel_event: "Any | None",
    ) -> tuple[str, bool]:
        """Tail the command's output file until it exits or the run is cancelled.

        Returns ``(output, cancelled)``. Always releases the session; on
        cancel it first kills the still-running process, so a stopped run
        does not leave its build burning CPU in the sandbox.
        """
        seen = ""
        tailing = True
        cancelled = False
        try:
            while True:
                # The run's stop signal, observed from INSIDE the command.
                # The stream loop in the worker only checks between tool
                # calls, so a run sitting in a long build could not be
                # stopped for hours (the 2026-11-01 incident). The event
                # is an asyncio.Event whose is_set() is a plain attribute
                # read, safe from this worker thread.
                if cancel_event is not None and _is_set(cancel_event):
                    cancelled = True
                    logger.info("streaming command cancelled by the run's stop signal")
                    break
                grew = False
                if tailing:
                    before = len(seen)
                    seen = self._emit_new_output(log_path, seen, on_chunk)
                    grew = len(seen) != before
                    if len(seen) > self._STREAM_MAX_TAIL_BYTES:
                        logger.debug("streaming output passed %d bytes; tailing off", self._STREAM_MAX_TAIL_BYTES)
                        tailing = False
                # Output arriving is already proof the command is alive, so
                # only spend a `view` request when it goes quiet. Halves the
                # HTTP traffic during the noisy part of a build -- httpx logs
                # every one of these at INFO, and the poll loop was ~25% of
                # all gateway log lines.
                if not grew and self._stream_status(session_id) != "running":
                    break
                time.sleep(self._STREAM_POLL_INTERVAL)

            # One last read: the final write lands between the previous poll
            # and the process exiting, so without this the last line of
            # every command would be missing from the live view.
            seen = self._emit_new_output(log_path, seen, on_chunk)
        finally:
            if cancelled:
                # Only queue the `rm -f` on the session once its process is
                # gone: on a still-busy session it would wait behind the very
                # command we are abandoning.
                killed = self._kill_stream_process(session_id)
                self._cleanup_stream_session(session_id, log_path if killed else None)
            else:
                self._cleanup_stream_session(session_id, log_path)
        return seen, cancelled

    def _kill_stream_process(self, session_id: str) -> bool:
        """Terminate the process in a streaming session. Never raises."""
        try:
            self._require_client().shell.kill_process(id=session_id)
            return True
        except Exception as e:  # noqa: BLE001 - a failed kill must not fail the cancel
            logger.warning("failed to kill cancelled streaming command in session %s: %s", session_id, e)
            return False

    def _blocking_fallback(self, command: str) -> str:
        """Run ``command`` on the blocking path after a failed streaming start.

        Must be called WITHOUT holding ``self._lock``. Runs the command exactly
        once: its result is the answer, whatever the command printed. The
        sandbox is declared unreachable only when the blocking call itself
        raises a transport error -- never because the command's own output
        mentions a refused connection.
        """
        try:
            return self._execute_command_raising(command)
        except Exception as e:  # noqa: BLE001
            if not _is_transport_error(e):
                logger.error(f"Failed to execute command in sandbox: {e}")
                return f"Error: {e}"
            logger.warning("sandbox %s unreachable on the blocking fallback: %s", self.id, e)
            if self._on_unreachable is not None:
                try:
                    self._on_unreachable(self.id)
                except Exception:  # noqa: BLE001
                    logger.debug("on_unreachable callback failed", exc_info=True)
            return f"Error: sandbox {self.id} is unreachable (connection refused); it may have been destroyed. Retry the command."

    def _emit_new_output(self, log_path: str, seen: str, on_chunk: "Callable[[str, bool], None]") -> str:
        """Report whatever the output file has gained since ``seen``."""
        content = self._read_stream_log(log_path)
        if content is None or content == seen:
            return seen
        if content.startswith(seen):
            on_chunk(content[len(seen) :], False)
        else:
            # The file should only ever grow, but a truncating writer would
            # otherwise have its output concatenated onto stale text.
            on_chunk(content, True)
        return content

    def _read_stream_log(self, log_path: str) -> "str | None":
        """Current contents of the output file, or None if it is not readable.

        Not an error: the file does not exist until the redirect is set up, so
        the first poll of every command lands before it.
        """
        try:
            result = self._require_client().file.read_file(file=log_path)
        except Exception as e:  # noqa: BLE001
            logger.debug("stream log %s not readable yet: %s", log_path, e)
            return None
        return (result.data.content or "") if result.data else ""

    def _stream_status(self, session_id: str) -> str:
        """`running` while the command is live; any other value is terminal.

        `no_change_timeout` and `hard_timeout` are terminal too -- the process
        is gone and no more output will arrive, so polling on would spin until
        the outer timeout.
        """
        try:
            data = self._require_client().shell.view(id=session_id).data
        except Exception as e:  # noqa: BLE001
            logger.warning("streaming view failed for session %s: %s", session_id, e)
            return "terminated"
        return (getattr(data, "status", None) or "running") if data else "running"

    def _cleanup_stream_session(self, session_id: str, log_path: "str | None" = None) -> None:
        """Release the per-command session and its output file.

        Best-effort: a leaked session or a stray file in the sandbox's /tmp
        costs a little memory, and failing the command over it would be worse.
        """
        if log_path:
            try:
                self._require_client().shell.exec_command(command=f"rm -f {shlex.quote(log_path)}", id=session_id, no_change_timeout=10)
            except Exception as e:  # noqa: BLE001
                logger.debug("failed to remove stream log %s: %s", log_path, e)
        try:
            self._require_client().shell.cleanup_session(session_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("failed to release streaming session %s: %s", session_id, e)

    def read_file(self, path: str) -> str:
        """Read the content of a file in the sandbox.

        Args:
            path: The absolute path of the file to read.

        Returns:
            The content of the file.
        """
        try:
            result = self._require_client().file.read_file(file=path)
            return result.data.content if result.data else ""
        except Exception as e:
            # A file that does not exist yet is an expected, handled outcome --
            # the dev-server tail polls its log from before the server's first
            # write, and `dev_server.py` treats "Error:" as empty. Logging that
            # at ERROR wrote a multi-line entry with full HTTP headers roughly
            # once a second and buried real failures.
            detail = str(e)
            if "does not exist" in detail or "status_code: 404" in detail:
                logger.debug("File not present (yet) in sandbox: %s", path)
            else:
                logger.error(f"Failed to read file in sandbox: {e}")
            return f"Error: {e}"

    def download_file(self, path: str) -> bytes:
        """Download file bytes from the sandbox.

        Raises:
            PermissionError: If the path contains '..' traversal segments or is
                outside ``VIRTUAL_PATH_PREFIX``.
            OSError: If the file cannot be retrieved from the sandbox.
        """
        # Reject path traversal before sending to the container API.
        # LocalSandbox gets this implicitly via _resolve_path;
        # here the path is forwarded verbatim so we must check explicitly.
        normalised = path.replace("\\", "/")
        for segment in normalised.split("/"):
            if segment == "..":
                logger.error(f"Refused download due to path traversal: {path}")
                raise PermissionError(f"Access denied: path traversal detected in '{path}'")

        stripped_path = normalised.lstrip("/")
        allowed_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
        if stripped_path != allowed_prefix and not stripped_path.startswith(f"{allowed_prefix}/"):
            logger.error("Refused download outside allowed directory: path=%s, allowed_prefix=%s", path, VIRTUAL_PATH_PREFIX)
            raise PermissionError(f"Access denied: path must be under '{VIRTUAL_PATH_PREFIX}': '{path}'")

        with self._lock:
            try:
                chunks: list[bytes] = []
                total = 0
                for chunk in self._require_client().file.download_file(path=path):
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_SIZE:
                        raise OSError(
                            errno.EFBIG,
                            f"File exceeds maximum download size of {_MAX_DOWNLOAD_SIZE} bytes",
                            path,
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
            except OSError:
                raise
            except Exception as e:
                logger.error(f"Failed to download file in sandbox: {e}")
                raise OSError(f"Failed to download file '{path}' from sandbox: {e}") from e

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """List the contents of a directory in the sandbox.

        Args:
            path: The absolute path of the directory to list.
            max_depth: The maximum depth to traverse. Default is 2.

        Returns:
            The contents of the directory.
        """
        with self._lock:
            try:
                result = self._require_client().shell.exec_command(command=f"find {shlex.quote(path)} -maxdepth {max_depth} -type f -o -type d 2>/dev/null | head -500", no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                output = result.data.output if result.data else ""
                if output:
                    return [line.strip() for line in output.strip().split("\n") if line.strip()]
                return []
            except Exception as e:
                logger.error(f"Failed to list directory in sandbox: {e}")
                return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write content to a file in the sandbox.

        Args:
            path: The absolute path of the file to write to.
            content: The text content to write to the file.
            append: Whether to append the content to the file.
        """
        with self._lock:
            try:
                if append:
                    existing = self.read_file(path)
                    if not existing.startswith("Error:"):
                        content = existing + content
                self._require_client().file.write_file(file=path, content=content)
            except Exception as e:
                logger.error(f"Failed to write file in sandbox: {e}")
                raise

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        if not include_dirs:
            result = self._require_client().file.find_files(path=path, glob=pattern)
            files = result.data.files if result.data and result.data.files else []
            filtered = [file_path for file_path in files if not should_ignore_path(file_path)]
            truncated = len(filtered) > max_results
            return filtered[:max_results], truncated

        result = self._require_client().file.list_path(path=path, recursive=True, show_hidden=False)
        entries = result.data.files if result.data and result.data.files else []
        matches: list[str] = []
        root_path = path.rstrip("/") or "/"
        root_prefix = root_path if root_path == "/" else f"{root_path}/"
        for entry in entries:
            if entry.path != root_path and not entry.path.startswith(root_prefix):
                continue
            if should_ignore_path(entry.path):
                continue
            rel_path = entry.path[len(root_path) :].lstrip("/")
            if path_matches(pattern, rel_path):
                matches.append(entry.path)
                if len(matches) >= max_results:
                    return matches, True
        return matches, False

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        import re as _re

        regex_source = _re.escape(pattern) if literal else pattern
        # Validate the pattern locally so an invalid regex raises re.error
        # (caught by grep_tool's except re.error handler) rather than a
        # generic remote API error.
        _re.compile(regex_source, 0 if case_sensitive else _re.IGNORECASE)
        regex = regex_source if case_sensitive else f"(?i){regex_source}"

        if glob is not None:
            find_result = self._require_client().file.find_files(path=path, glob=glob)
            candidate_paths = find_result.data.files if find_result.data and find_result.data.files else []
        else:
            list_result = self._require_client().file.list_path(path=path, recursive=True, show_hidden=False)
            entries = list_result.data.files if list_result.data and list_result.data.files else []
            candidate_paths = [entry.path for entry in entries if not entry.is_directory]

        matches: list[GrepMatch] = []
        truncated = False

        for file_path in candidate_paths:
            if should_ignore_path(file_path):
                continue

            search_result = self._require_client().file.search_in_file(file=file_path, regex=regex)
            data = search_result.data
            if data is None:
                continue

            line_numbers = data.line_numbers or []
            matched_lines = data.matches or []
            for line_number, line in zip(line_numbers, matched_lines):
                matches.append(
                    GrepMatch(
                        path=file_path,
                        line_number=line_number if isinstance(line_number, int) else 0,
                        line=truncate_line(line),
                    )
                )
                if len(matches) >= max_results:
                    truncated = True
                    return matches, truncated

        return matches, truncated

    def update_file(self, path: str, content: bytes) -> None:
        """Update a file with binary content in the sandbox.

        Args:
            path: The absolute path of the file to update.
            content: The binary content to write to the file.
        """
        with self._lock:
            try:
                base64_content = base64.b64encode(content).decode("utf-8")
                self._require_client().file.write_file(file=path, content=base64_content, encoding="base64")
            except Exception as e:
                logger.error(f"Failed to update file in sandbox: {e}")
                raise
