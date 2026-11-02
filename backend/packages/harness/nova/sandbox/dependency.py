"""Dependency injection provider for sandbox tools.

This module provides functions to create and manage ToolContext instances
for dependency injection in sandbox tools.
"""

from __future__ import annotations

from typing import Any

from deerflow.sandbox.tool_context import ToolContext, create_tool_context


def get_tool_context(
    thread_id: str = "",
    thread_data: dict[str, Any] | None = None,
    runtime_state: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> ToolContext:
    """Get a ToolContext for the current request.

    This is the primary dependency injection point for sandbox tools.
    In production, it creates a new context from the current runtime state.
    In tests, it can be overridden to provide mock dependencies.

    Args:
        thread_id: The current thread ID.
        thread_data: Thread-specific data (paths, etc.).
        runtime_state: Mutable runtime state dict.
        runtime_context: Mutable runtime context dict.
        runtime_config: Runtime configuration dict.

    Returns:
        A ToolContext instance configured for the current request.
    """
    return create_tool_context(
        thread_id=thread_id,
        thread_data=thread_data,
        runtime_state=runtime_state,
        runtime_context=runtime_context,
        runtime_config=runtime_config,
    )


# Registry for dependency overrides (useful for testing)
_context_overrides: dict[str, ToolContext] = {}


def override_tool_context(name: str, context: ToolContext) -> None:
    """Override a ToolContext by name for testing.

    This allows tests to inject mock dependencies without global singleton mocking.

    Args:
        name: The name to associate with the context override.
        context: The ToolContext instance to use.
    """
    _context_overrides[name] = context


def get_overridden_tool_context(name: str) -> ToolContext | None:
    """Get an overridden ToolContext by name.

    Args:
        name: The name of the context override to retrieve.

    Returns:
        The overridden ToolContext, or None if not found.
    """
    return _context_overrides.get(name)


def clear_overrides() -> None:
    """Clear all context overrides.

    This should be called in test teardown to prevent state leakage between tests.
    """
    _context_overrides.clear()


class ToolContextProvider:
    """Provider class for ToolContext instances.

    This class encapsulates the logic for creating and managing ToolContext
    instances, making it easy to swap implementations for testing.
    """

    def __init__(self) -> None:
        """Initialize the provider."""
        self._overrides: dict[str, ToolContext] = {}

    def get_context(
        self,
        thread_id: str = "",
        thread_data: dict[str, Any] | None = None,
        runtime_state: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
        runtime_config: dict[str, Any] | None = None,
    ) -> ToolContext:
        """Get a ToolContext for the current request.

        Args:
            thread_id: The current thread ID.
            thread_data: Thread-specific data (paths, etc.).
            runtime_state: Mutable runtime state dict.
            runtime_context: Mutable runtime context dict.
            runtime_config: Runtime configuration dict.

        Returns:
            A ToolContext instance configured for the current request.
        """
        return create_tool_context(
            thread_id=thread_id,
            thread_data=thread_data,
            runtime_state=runtime_state,
            runtime_context=runtime_context,
            runtime_config=runtime_config,
        )

    def override(self, name: str, context: ToolContext) -> None:
        """Override a ToolContext by name for testing.

        Args:
            name: The name to associate with the context override.
            context: The ToolContext instance to use.
        """
        self._overrides[name] = context

    def get_override(self, name: str) -> ToolContext | None:
        """Get an overridden ToolContext by name.

        Args:
            name: The name of the context override to retrieve.

        Returns:
            The overridden ToolContext, or None if not found.
        """
        return self._overrides.get(name)

    def clear_overrides(self) -> None:
        """Clear all context overrides."""
        self._overrides.clear()


# Default provider instance
default_provider = ToolContextProvider()
