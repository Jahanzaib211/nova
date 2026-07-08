"""Tool context for dependency injection in sandbox tools.

This module provides a `ToolContext` dataclass that encapsulates all dependencies
needed by sandbox tools, enabling independent testability without global singletons.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig
    from deerflow.config.paths import PathResolver
    from deerflow.sandbox.file_operation_lock import FileOperationLock
    from deerflow.sandbox.sandbox import Sandbox
    from deerflow.sandbox.sandbox_provider import SandboxProvider


@dataclass(frozen=False)
class ToolContext:
    """Dependencies for sandbox tools.

    This dataclass encapsulates all external dependencies that sandbox tools need,
    making them independently testable without global singleton mocking.

    Attributes:
        sandbox_provider: Provider for acquiring/releasing sandboxes.
        sandbox: The active sandbox instance (lazy-initialized).
        sandbox_id: The current sandbox ID.
        thread_id: The current thread ID.
        thread_data: Thread-specific data (paths, etc.).
        runtime_state: Mutable runtime state dict.
        runtime_context: Mutable runtime context dict.
        runtime_config: Runtime configuration dict.
        file_lock: Lock for file operations.
        config: Application configuration.
        paths: Path resolver for virtual paths.
    """

    # Sandbox dependencies
    sandbox_provider: Any = None  # SandboxProvider
    sandbox: Any = None  # Sandbox
    sandbox_id: str = ""

    # Thread context
    thread_id: str = ""
    thread_data: dict[str, Any] = field(default_factory=dict)

    # Runtime state
    runtime_state: dict[str, Any] = field(default_factory=dict)
    runtime_context: dict[str, Any] = field(default_factory=dict)
    runtime_config: dict[str, Any] = field(default_factory=dict)

    # Configuration dependencies
    config: Any = None  # AppConfig
    paths: Any = None  # PathResolver

    # Lock for file operations
    file_lock: Any = None  # FileOperationLock

    # Lazy initialization flag
    _initialized: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ensure_initialized(self) -> None:
        """Ensure sandbox is initialized, acquiring lazily if needed."""
        if self._initialized and self.sandbox is not None:
            return

        with self._lock:
            if self._initialized and self.sandbox is not None:
                return

            if self.sandbox_provider is None:
                from deerflow.sandbox.sandbox_provider import get_sandbox_provider

                self.sandbox_provider = get_sandbox_provider()

            # Check if sandbox already exists
            if self.sandbox_id:
                self.sandbox = self.sandbox_provider.get(self.sandbox_id)
                if self.sandbox is not None:
                    self._initialized = True
                    return

            # Lazy acquisition
            if not self.thread_id:
                raise ValueError("Thread ID not available in tool context")

            self.sandbox_id = self.sandbox_provider.acquire(self.thread_id)
            self.sandbox = self.sandbox_provider.get(self.sandbox_id)

            if self.sandbox is None:
                raise RuntimeError(f"Sandbox not found after acquisition: {self.sandbox_id}")

            self.runtime_state["sandbox"] = {"sandbox_id": self.sandbox_id}
            self.runtime_context["sandbox_id"] = self.sandbox_id
            self._initialized = True

    def ensure_thread_directories_exist(self) -> None:
        """Ensure thread data directories exist for local sandbox."""
        if not self.thread_data:
            return

        # Check if directories have already been created
        if self.runtime_state.get("thread_directories_created"):
            return

        # Create the three directories
        import os

        for key in ["workspace_path", "uploads_path", "outputs_path"]:
            path = self.thread_data.get(key)
            if path:
                os.makedirs(path, exist_ok=True)

        # Mark as created to avoid redundant operations
        self.runtime_state["thread_directories_created"] = True

    def is_local_sandbox(self) -> bool:
        """Check if the current sandbox is a local sandbox."""
        if not self.sandbox_id:
            return False
        return self.sandbox_id == "local" or self.sandbox_id.startswith("local:")

    @property
    def is_initialized(self) -> bool:
        """Check if the context is fully initialized."""
        return self._initialized and self.sandbox is not None


def create_tool_context(
    thread_id: str = "",
    thread_data: dict[str, Any] | None = None,
    runtime_state: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> ToolContext:
    """Create a new ToolContext with the given parameters.

    This is the primary entry point for creating tool contexts in production.
    For testing, use `create_test_tool_context` instead.

    Args:
        thread_id: The current thread ID.
        thread_data: Thread-specific data (paths, etc.).
        runtime_state: Mutable runtime state dict.
        runtime_context: Mutable runtime context dict.
        runtime_config: Runtime configuration dict.

    Returns:
        A new ToolContext instance.
    """
    return ToolContext(
        thread_id=thread_id,
        thread_data=thread_data or {},
        runtime_state=runtime_state or {},
        runtime_context=runtime_context or {},
        runtime_config=runtime_config or {},
    )


def create_test_tool_context(
    sandbox: Any = None,
    sandbox_id: str = "test-sandbox",
    thread_id: str = "test-thread",
    thread_data: dict[str, Any] | None = None,
    runtime_state: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> ToolContext:
    """Create a ToolContext for testing with pre-configured dependencies.

    This is the primary entry point for creating tool contexts in tests.
    It allows injecting mock dependencies without global singleton mocking.

    Args:
        sandbox: Mock sandbox instance.
        sandbox_id: The sandbox ID to use.
        thread_id: The thread ID to use.
        thread_data: Thread-specific data (paths, etc.).
        runtime_state: Mutable runtime state dict.
        runtime_context: Mutable runtime context dict.
        runtime_config: Runtime configuration dict.

    Returns:
        A new ToolContext instance configured for testing.
    """
    if thread_data is None:
        thread_data = {
            "workspace_path": "/tmp/test/threads/test-thread/user-data/workspace",
            "uploads_path": "/tmp/test/threads/test-thread/user-data/uploads",
            "outputs_path": "/tmp/test/threads/test-thread/user-data/outputs",
        }

    return ToolContext(
        sandbox=sandbox,
        sandbox_id=sandbox_id,
        thread_id=thread_id,
        thread_data=thread_data,
        runtime_state=runtime_state or {},
        runtime_context=runtime_context or {},
        runtime_config=runtime_config or {},
        _initialized=sandbox is not None,
    )
