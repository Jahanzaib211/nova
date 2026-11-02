"""Thread-safe network utilities."""

import socket
import threading
from contextlib import contextmanager


class PortAllocator:
    """Thread-safe port allocator that prevents port conflicts in concurrent environments.

    This class maintains a set of reserved ports and uses a lock to ensure that
    port allocation is atomic. Once a port is allocated, it remains reserved until
    explicitly released.

    Usage:
        allocator = PortAllocator()

        # Option 1: Manual allocation and release
        port = allocator.allocate(start_port=8080)
        try:
            # Use the port...
        finally:
            allocator.release(port)

        # Option 2: Context manager (recommended)
        with allocator.allocate_context(start_port=8080) as port:
            # Use the port...
            # Port is automatically released when exiting the context
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reserved_ports: set[int] = set()

    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available for binding.

        Args:
            port: The port number to check.

        Returns:
            True if the port is available, False otherwise.
        """
        if port in self._reserved_ports:
            return False

        # Bind to 0.0.0.0 (wildcard) rather than localhost so that the check
        # mirrors what Docker does on the same network namespace. Docker binds
        # 0.0.0.0:PORT; checking only 127.0.0.1 can falsely report a port as
        # available when Docker occupies it on the wildcard address.
        #
        # LIMITATION (DooD): when this process runs inside a container and asks
        # the HOST Docker daemon to publish ports, this bind test inspects the
        # CONTAINER's namespace — host-side listeners are invisible and the
        # check false-positives. Callers that publish host ports must treat
        # Docker's own "address already in use" rejection as authoritative and
        # quarantine the rejected port via reserve() before retrying.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

    def allocate(self, start_port: int = 8080, max_range: int = 100) -> int:
        """Allocate an available port in a thread-safe manner.

        This method is thread-safe. It finds an available port, marks it as reserved,
        and returns it. The port remains reserved until release() is called.

        Args:
            start_port: The port number to start searching from.
            max_range: Maximum number of ports to search.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If no available port is found in the specified range.
        """
        with self._lock:
            for port in range(start_port, start_port + max_range):
                if self._is_port_available(port):
                    self._reserved_ports.add(port)
                    return port

            raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_range}")

    def release(self, port: int) -> None:
        """Release a previously allocated port.

        Args:
            port: The port number to release.
        """
        with self._lock:
            self._reserved_ports.discard(port)

    def reserve(self, port: int) -> None:
        """Quarantine a port so allocate() never returns it.

        Used when an external authority (the host Docker daemon in DooD
        setups) rejects a port this process believed was free — the local
        bind test cannot see host-side listeners, so the rejection is the
        only reliable signal. The reservation lasts for the process
        lifetime unless release() is called.
        """
        with self._lock:
            self._reserved_ports.add(port)

    @contextmanager
    def allocate_context(self, start_port: int = 8080, max_range: int = 100):
        """Context manager for port allocation with automatic release.

        Args:
            start_port: The port number to start searching from.
            max_range: Maximum number of ports to search.

        Yields:
            An available port number.
        """
        port = self.allocate(start_port, max_range)
        try:
            yield port
        finally:
            self.release(port)


# Global port allocator instance for shared use across the application
_global_port_allocator = PortAllocator()


def get_free_port(start_port: int = 8080, max_range: int = 100) -> int:
    """Get a free port in a thread-safe manner.

    This function uses a global port allocator to ensure that concurrent calls
    don't return the same port. The port is marked as reserved until release_port()
    is called.

    Args:
        start_port: The port number to start searching from.
        max_range: Maximum number of ports to search.

    Returns:
        An available port number.

    Raises:
        RuntimeError: If no available port is found in the specified range.
    """
    return _global_port_allocator.allocate(start_port, max_range)


def release_port(port: int) -> None:
    """Release a previously allocated port.

    Args:
        port: The port number to release.
    """
    _global_port_allocator.release(port)


def reserve_port(port: int) -> None:
    """Quarantine a port so get_free_port() never returns it (see PortAllocator.reserve)."""
    _global_port_allocator.reserve(port)
