"""Tests for ToolContext dependency injection."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from deerflow.sandbox.tool_context import (
    ToolContext,
    create_test_tool_context,
    create_tool_context,
)


class TestToolContextCreation:
    """Tests for ToolContext creation."""

    def test_create_tool_context_defaults(self) -> None:
        """ToolContext should have sensible defaults."""
        ctx = ToolContext()

        assert ctx.thread_id == ""
        assert ctx.thread_data == {}
        assert ctx.runtime_state == {}
        assert ctx.runtime_context == {}
        assert ctx.runtime_config == {}
        assert ctx._initialized is False
        assert ctx.sandbox is None
        assert ctx.sandbox_id == ""

    def test_create_tool_context_with_params(self) -> None:
        """ToolContext should accept custom parameters."""
        thread_data = {"workspace_path": "/tmp/workspace"}
        ctx = ToolContext(
            thread_id="t1",
            thread_data=thread_data,
            runtime_state={"key": "value"},
        )

        assert ctx.thread_id == "t1"
        assert ctx.thread_data == thread_data
        assert ctx.runtime_state == {"key": "value"}

    def test_create_test_tool_context(self) -> None:
        """create_test_tool_context should create a context for testing."""
        sandbox = MagicMock()
        ctx = create_test_tool_context(sandbox=sandbox, thread_id="test-thread")

        assert ctx.sandbox is sandbox
        assert ctx.thread_id == "test-thread"
        assert ctx._initialized is True
        assert ctx.is_initialized is True

    def test_create_test_tool_context_defaults(self) -> None:
        """create_test_tool_context should have sensible defaults."""
        ctx = create_test_tool_context()

        assert ctx.sandbox_id == "test-sandbox"
        assert ctx.thread_id == "test-thread"
        assert ctx.thread_data["workspace_path"].startswith("/tmp/test")
        assert ctx._initialized is False

    def test_create_test_tool_context_custom_thread_data(self) -> None:
        """create_test_tool_context should accept custom thread data."""
        thread_data = {"workspace_path": "/custom/workspace"}
        ctx = create_test_tool_context(thread_data=thread_data)

        assert ctx.thread_data == thread_data


class TestToolContextInitialization:
    """Tests for ToolContext lazy initialization."""

    def test_ensure_initialized_with_existing_sandbox(self) -> None:
        """ensure_initialized should not re-acquire if sandbox exists."""
        sandbox = MagicMock()
        ctx = create_test_tool_context(sandbox=sandbox, sandbox_id="existing")

        ctx.ensure_initialized()

        assert ctx.sandbox is sandbox
        assert ctx.sandbox_id == "existing"

    def test_ensure_initialized_raises_without_thread_id(self) -> None:
        """ensure_initialized should raise if thread_id is missing."""
        ctx = ToolContext()

        with pytest.raises(ValueError, match="Thread ID not available"):
            ctx.ensure_initialized()

    def test_ensure_thread_directories_exist(self) -> None:
        """ensure_thread_directories_exist should create directories."""
        ctx = create_test_tool_context()
        ctx.thread_data = {
            "workspace_path": "/tmp/test/workspace",
            "uploads_path": "/tmp/test/uploads",
            "outputs_path": "/tmp/test/outputs",
        }

        with patch("os.makedirs") as mock_makedirs:
            ctx.ensure_thread_directories_exist()

            assert mock_makedirs.call_count == 3
            assert ctx.runtime_state["thread_directories_created"] is True

    def test_ensure_thread_directories_exist_skips_if_already_done(self) -> None:
        """ensure_thread_directories_exist should skip if already done."""
        ctx = create_test_tool_context()
        ctx.runtime_state["thread_directories_created"] = True

        with patch("os.makedirs") as mock_makedirs:
            ctx.ensure_thread_directories_exist()

            mock_makedirs.assert_not_called()


class TestToolContextLocalSandbox:
    """Tests for ToolContext local sandbox detection."""

    def test_is_local_sandbox_local(self) -> None:
        """is_local_sandbox should return True for 'local' sandbox."""
        ctx = create_test_tool_context(sandbox_id="local")

        assert ctx.is_local_sandbox() is True

    def test_is_local_sandbox_local_with_thread(self) -> None:
        """is_local_sandbox should return True for 'local:{thread_id}' format."""
        ctx = create_test_tool_context(sandbox_id="local:thread-123")

        assert ctx.is_local_sandbox() is True

    def test_is_local_sandbox_container(self) -> None:
        """is_local_sandbox should return False for container sandbox."""
        ctx = create_test_tool_context(sandbox_id="abc123")

        assert ctx.is_local_sandbox() is False

    def test_is_local_sandbox_empty(self) -> None:
        """is_local_sandbox should return False for empty sandbox_id."""
        ctx = create_test_tool_context(sandbox_id="")

        assert ctx.is_local_sandbox() is False


class TestToolContextThreadSafety:
    """Tests for ToolContext thread safety."""

    def test_concurrent_ensure_initialized(self) -> None:
        """ensure_initialized should be thread-safe."""
        ctx = ToolContext()
        ctx.thread_id = "test-thread"
        call_count = 0

        def mock_acquire(thread_id: str) -> str:
            nonlocal call_count
            call_count += 1
            return "sandbox-1"

        mock_provider = MagicMock()
        mock_provider.acquire = mock_acquire
        mock_provider.get.return_value = MagicMock()
        ctx.sandbox_provider = mock_provider

        threads = []
        for _ in range(10):
            t = threading.Thread(target=ctx.ensure_initialized)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # acquire should only be called once due to locking
        assert call_count == 1
        assert ctx.sandbox is not None


class TestToolContextIntegration:
    """Integration tests for ToolContext with mock dependencies."""

    def test_tool_context_with_mock_sandbox(self) -> None:
        """ToolContext should work with mock sandbox."""
        sandbox = MagicMock()
        sandbox.read_file.return_value = "file content"
        sandbox.execute_command.return_value = "command output"

        ctx = create_test_tool_context(sandbox=sandbox)
        ctx.ensure_initialized()

        assert ctx.is_initialized
        assert ctx.sandbox.read_file("test.txt") == "file content"
        assert ctx.sandbox.execute_command("echo test") == "command output"

    def test_tool_context_state_management(self) -> None:
        """ToolContext should manage state correctly."""
        ctx = create_test_tool_context()

        ctx.runtime_state["key"] = "value"
        ctx.runtime_context["another_key"] = "another_value"

        assert ctx.runtime_state["key"] == "value"
        assert ctx.runtime_context["another_key"] == "another_value"


@pytest.fixture
def mock_sandbox():
    """Create a mock sandbox for testing."""
    sandbox = MagicMock()
    sandbox.read_file.return_value = "test content"
    sandbox.write_file.return_value = None
    sandbox.execute_command.return_value = "output"
    sandbox.list_dir.return_value = ["file1.txt", "file2.txt"]
    return sandbox


@pytest.fixture
def tool_context(mock_sandbox):
    """Create a ToolContext with mock dependencies for testing."""
    return create_test_tool_context(
        sandbox=mock_sandbox,
        thread_id="test-thread",
        thread_data={
            "workspace_path": "/tmp/test/workspace",
            "uploads_path": "/tmp/test/uploads",
            "outputs_path": "/tmp/test/outputs",
        },
    )


class TestToolContextWithFixtures:
    """Tests using pytest fixtures."""

    def test_fixture_tool_context(self, tool_context, mock_sandbox) -> None:
        """ToolContext fixture should work correctly."""
        assert tool_context.sandbox is mock_sandbox
        assert tool_context.thread_id == "test-thread"
        assert tool_context.is_initialized

    def test_fixture_tool_context_thread_data(self, tool_context) -> None:
        """ToolContext fixture should have correct thread data."""
        assert tool_context.thread_data["workspace_path"] == "/tmp/test/workspace"
        assert tool_context.thread_data["uploads_path"] == "/tmp/test/uploads"
        assert tool_context.thread_data["outputs_path"] == "/tmp/test/outputs"
