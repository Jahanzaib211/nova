"""Tests for dependency injection provider."""

from unittest.mock import MagicMock

import pytest

from deerflow.sandbox.dependency import (
    ToolContextProvider,
    clear_overrides,
    get_overridden_tool_context,
    get_tool_context,
    override_tool_context,
)
from deerflow.sandbox.tool_context import ToolContext, create_test_tool_context


class TestGetToolContext:
    """Tests for get_tool_context function."""

    def test_get_tool_context_defaults(self) -> None:
        """get_tool_context should return a context with defaults."""
        ctx = get_tool_context()

        assert isinstance(ctx, ToolContext)
        assert ctx.thread_id == ""
        assert ctx.thread_data == {}
        assert ctx.runtime_state == {}

    def test_get_tool_context_with_params(self) -> None:
        """get_tool_context should accept custom parameters."""
        thread_data = {"workspace_path": "/tmp/workspace"}
        ctx = get_tool_context(
            thread_id="t1",
            thread_data=thread_data,
            runtime_state={"key": "value"},
        )

        assert ctx.thread_id == "t1"
        assert ctx.thread_data == thread_data
        assert ctx.runtime_state == {"key": "value"}


class TestContextOverrides:
    """Tests for context override functions."""

    def test_override_and_get(self) -> None:
        """override_tool_context should store context, get_overridden_tool_context should retrieve it."""
        ctx = create_test_tool_context(thread_id="test")
        override_tool_context("test-ctx", ctx)

        retrieved = get_overridden_tool_context("test-ctx")
        assert retrieved is ctx
        assert retrieved.thread_id == "test"

    def test_get_nonexistent_override(self) -> None:
        """get_overridden_tool_context should return None for nonexistent name."""
        retrieved = get_overridden_tool_context("nonexistent")
        assert retrieved is None

    def test_clear_overrides(self) -> None:
        """clear_overrides should remove all overrides."""
        ctx = create_test_tool_context()
        override_tool_context("test-ctx", ctx)

        clear_overrides()

        retrieved = get_overridden_tool_context("test-ctx")
        assert retrieved is None


class TestToolContextProvider:
    """Tests for ToolContextProvider class."""

    def test_get_context_defaults(self) -> None:
        """get_context should return a context with defaults."""
        provider = ToolContextProvider()
        ctx = provider.get_context()

        assert isinstance(ctx, ToolContext)
        assert ctx.thread_id == ""

    def test_get_context_with_params(self) -> None:
        """get_context should accept custom parameters."""
        provider = ToolContextProvider()
        thread_data = {"workspace_path": "/tmp/workspace"}
        ctx = provider.get_context(
            thread_id="t1",
            thread_data=thread_data,
        )

        assert ctx.thread_id == "t1"
        assert ctx.thread_data == thread_data

    def test_override_and_get(self) -> None:
        """override should store context, get_override should retrieve it."""
        provider = ToolContextProvider()
        ctx = create_test_tool_context(thread_id="test")
        provider.override("test-ctx", ctx)

        retrieved = provider.get_override("test-ctx")
        assert retrieved is ctx
        assert retrieved.thread_id == "test"

    def test_get_nonexistent_override(self) -> None:
        """get_override should return None for nonexistent name."""
        provider = ToolContextProvider()
        retrieved = provider.get_override("nonexistent")
        assert retrieved is None

    def test_clear_overrides(self) -> None:
        """clear_overrides should remove all overrides."""
        provider = ToolContextProvider()
        ctx = create_test_tool_context()
        provider.override("test-ctx", ctx)

        provider.clear_overrides()

        retrieved = provider.get_override("test-ctx")
        assert retrieved is None

    def test_multiple_providers_independent(self) -> None:
        """Different provider instances should have independent overrides."""
        provider1 = ToolContextProvider()
        provider2 = ToolContextProvider()

        ctx1 = create_test_tool_context(thread_id="t1")
        ctx2 = create_test_tool_context(thread_id="t2")

        provider1.override("shared", ctx1)
        provider2.override("shared", ctx2)

        assert provider1.get_override("shared") is ctx1
        assert provider2.get_override("shared") is ctx2


@pytest.fixture
def tool_context():
    """Create a ToolContext for testing."""
    return create_test_tool_context(thread_id="fixture-thread")


@pytest.fixture
def provider():
    """Create a ToolContextProvider for testing."""
    return ToolContextProvider()


class TestToolContextProviderWithFixtures:
    """Tests using pytest fixtures."""

    def test_fixture_tool_context(self, tool_context) -> None:
        """ToolContext fixture should work correctly."""
        assert tool_context.thread_id == "fixture-thread"

    def test_fixture_provider(self, provider) -> None:
        """Provider fixture should work correctly."""
        ctx = provider.get_context()
        assert isinstance(ctx, ToolContext)

    def test_provider_with_fixture_context(self, provider, tool_context) -> None:
        """Provider should work with fixture contexts."""
        provider.override("test", tool_context)
        retrieved = provider.get_override("test")
        assert retrieved is tool_context
