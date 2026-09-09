from abc import ABC, abstractmethod
from collections.abc import Callable

from deerflow.sandbox.search import GrepMatch


class Sandbox(ABC):
    """Abstract base class for sandbox environments"""

    _id: str

    def __init__(self, id: str):
        self._id = id

    @property
    def id(self) -> str:
        return self._id

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """Execute bash command in sandbox.

        Args:
            command: The command to execute.

        Returns:
            The standard or error output of the command.
        """
        pass

    @property
    def closed(self) -> bool:
        """Whether this sandbox has been released and can no longer be used.

        Exists so callers can ask directly instead of inferring it from an error
        message. ``dev_server``'s log tail used to detect a released sandbox by
        searching a returned string for the words "has no attribute" -- coupling
        a control-flow decision to the wording of an exception, which any
        rewording would silently break.
        """
        return False

    #: Whether this backend can report command output before the command exits.
    #: Deliberately opt-in and *not* abstract: a backend that cannot stream is
    #: not broken, and forcing every implementation (including test doubles) to
    #: grow a method to stay instantiable would be a tax on the common case.
    supports_streaming: bool = False

    def execute_command_streaming(self, command: str, on_chunk: "Callable[[str, bool], None]") -> str:
        """Execute a command, reporting output as it is produced.

        ``on_chunk(text, replace)`` is called for each change in output:
        ``replace=False`` appends ``text``; ``replace=True`` says the previous
        body is void and ``text`` is the whole of the new one. Appending is the
        normal case; ``replace`` exists so a backend whose output can be
        rewritten rather than extended -- a truncated buffer, a redrawn screen
        -- cannot have stale text concatenated onto fresh. Returns the complete
        output, exactly as :meth:`execute_command` would.

        Only meaningful when :attr:`supports_streaming` is True. Callers must
        check that flag rather than catching this error.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support streaming execution")

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read the content of a file.

        Args:
            path: The absolute path of the file to read.

        Returns:
            The content of the file.
        """
        pass

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """Download the binary content of a file.

        Args:
            path: The absolute path of the file to download.

        Returns:
            Raw file bytes.

        Raises:
            PermissionError: If path traversal is detected or the path is outside
                the allowed virtual prefix.
            OSError: If the file cannot be read or does not exist.  Both local
                and remote implementations must raise ``OSError`` so callers
                have a single exception type to handle.
        """
        pass

    @abstractmethod
    def list_dir(self, path: str, max_depth=2) -> list[str]:
        """List the contents of a directory.

        Args:
            path: The absolute path of the directory to list.
            max_depth: The maximum depth to traverse. Default is 2.

        Returns:
            The contents of the directory.
        """
        pass

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write content to a file.

        Args:
            path: The absolute path of the file to write to.
            content: The text content to write to the file.
            append: Whether to append the content to the file. If False, the file will be created or overwritten.
        """
        pass

    @abstractmethod
    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """Find paths that match a glob pattern under a root directory."""
        pass

    @abstractmethod
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
        """Search for matches inside text files under a directory."""
        pass

    @abstractmethod
    def update_file(self, path: str, content: bytes) -> None:
        """Update a file with binary content.

        Args:
            path: The absolute path of the file to update.
            content: The binary content to write to the file.
        """
        pass
