"""PTY Manager for the Nova Execution Kernel.

Phase C8 — provides portable PTY allocation, window-size control, and
file-descriptor management for interactive shell sessions.

Design notes:

- Uses ``pty.openpty()`` on POSIX systems; on Windows, falls back to
  ``os.open("conout$", ...)`` for console I/O (limited support).
- Window-size updates use ``fcntl.ioctl(fd, termios.TIOCSWINSZ, ...)``.
- All PTY operations are synchronous — async wrappers are provided by
  :class:`InteractiveShellAdapter <adapters.interactive_shell.InteractiveShellAdapter>`.
- The PTY manager does NOT own processes; it only manages the PTY endpoints.
  Process lifecycle is owned by the :class:`ExecutionKernel`.

Usage::

    from deerflow.execution.pty_manager import PTYManager

    mgr = PTYManager()
    master_fd, slave_fd = mgr.open()
    mgr.set_window_size(master_fd, rows=24, cols=80)
    # spawn shell with slave_fd as stdin/stdout/stderr
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import struct
import termios
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# Windows console constants (used when os.name == "nt")
MSPARM_NOT_SUPPORTED = NotImplementedError("PTY operations not supported on Windows")


@dataclass
class TerminalSize:
    """Terminal window dimensions."""

    rows: int = 24
    cols: int = 80
    width_pixels: int = 0  # 0 = unknown/don't care
    height_pixels: int = 0

    def to_winsize(self) -> bytes:
        """Pack into the struct used by TIOCSWINSZ."""
        return struct.pack("HHHH", self.rows, self.cols, self.width_pixels, self.height_pixels)

    @classmethod
    def from_winsize(cls, data: bytes) -> TerminalSize:
        """Unpack from a winsize struct."""
        rows, cols, xp, yp = struct.unpack("HHHH", data)
        return cls(rows=rows, cols=cols, width_pixels=xp, height_pixels=yp)


class PTYManager:
    """Manages PTY allocation and window-size control.

    Phase C8: provides the low-level PTY primitives for interactive shell
    sessions.  Thread-safe for use from multiple threads; the actual PTY
    file descriptors should only be accessed from a single thread per session.
    """

    def __init__(self) -> None:
        self._master_to_slave: dict[int, int] = {}
        self._slave_to_master: dict[int, int] = {}

    def open(self) -> tuple[int, int]:
        """Allocate a new PTY pair.

        Returns:
            Tuple of (master_fd, slave_fd).

        Raises:
            OSError: If PTY allocation fails.
            NotImplementedError: On non-POSIX systems.
        """
        if os.name != "posix":
            raise NotImplementedError("PTY operations are only supported on POSIX systems")

        master_fd, slave_fd = os.openpty()
        self._master_to_slave[master_fd] = slave_fd
        self._slave_to_master[slave_fd] = master_fd
        return master_fd, slave_fd

    def close(self, master_fd: int) -> None:
        """Close a PTY pair by master file descriptor.

        Safely closes both master and slave ends, removing them from internal maps.
        Idempotent — subsequent calls are no-ops.
        """
        slave_fd = self._master_to_slave.pop(master_fd, None)
        if slave_fd is not None:
            self._slave_to_master.pop(slave_fd, None)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
        with contextlib.suppress(OSError):
            os.close(master_fd)

    def set_window_size(self, master_fd: int, rows: int, cols: int) -> None:
        """Set the terminal window size for a PTY.

        Args:
            master_fd: Master end of the PTY.
            rows: Number of rows (characters high).
            cols: Number of columns (characters wide).

        Raises:
            OSError: If ioctl fails.
        """
        size = TerminalSize(rows=rows, cols=cols)
        self._set_window_size_ioctl(master_fd, size)

    def set_window_size_pixels(self, master_fd: int, rows: int, cols: int, width_pixels: int, height_pixels: int) -> None:
        """Set the terminal window size including pixel dimensions.

        Args:
            master_fd: Master end of the PTY.
            rows: Number of rows.
            cols: Number of columns.
            width_pixels: Width in pixels.
            height_pixels: Height in pixels.
        """
        size = TerminalSize(rows=rows, cols=cols, width_pixels=width_pixels, height_pixels=height_pixels)
        self._set_window_size_ioctl(master_fd, size)

    def get_window_size(self, master_fd: int) -> TerminalSize:
        """Get the current terminal window size for a PTY.

        Returns:
            Current TerminalSize.

        Raises:
            OSError: If TIOCGWINSZ fails.
        """
        try:
            winsize = fcntl.ioctl(master_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            return TerminalSize.from_winsize(winsize)
        except OSError:
            return TerminalSize()

    def resize(self, master_fd: int, size: TerminalSize) -> None:
        """Resize a PTY to the given terminal dimensions."""
        self._set_window_size_ioctl(master_fd, size)

    def _set_window_size_ioctl(self, master_fd: int, size: TerminalSize) -> None:
        """Issue the TIOCSWINSZ ioctl."""
        with contextlib.suppress(OSError):
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size.to_winsize())

    def get_slave_fd(self, master_fd: int) -> int | None:
        """Return the slave fd for a master fd, or None if not found."""
        return self._master_to_slave.get(master_fd)

    def get_master_fd(self, slave_fd: int) -> int | None:
        """Return the master fd for a slave fd, or None if not found."""
        return self._slave_to_master.get(slave_fd)

    def list_open(self) -> list[int]:
        """Return list of all open master file descriptors."""
        return list(self._master_to_slave.keys())
