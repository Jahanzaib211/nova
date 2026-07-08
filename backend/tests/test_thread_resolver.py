"""Tests for ThreadResolver service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.message_bus import InboundMessage
from app.channels.services.thread_resolver import ThreadResolver


@pytest.fixture
def mock_store():
    """Create a mock ChannelStore."""
    store = MagicMock()
    store.get_thread_id.return_value = None
    store.set_thread_id.return_value = None
    return store


@pytest.fixture
def mock_connection_repo():
    """Create a mock connection repository."""
    repo = AsyncMock()
    repo.get_thread_id.return_value = None
    repo.set_thread_id.return_value = None
    return repo


@pytest.fixture
def thread_resolver(mock_store, mock_connection_repo):
    """Create a ThreadResolver with mock dependencies."""
    return ThreadResolver(store=mock_store, connection_repo=mock_connection_repo)


@pytest.fixture
def inbound_message():
    """Create a test InboundMessage."""
    return InboundMessage(
        channel_name="slack",
        chat_id="C123",
        user_id="U123",
        text="Hello",
        msg_type="chat",
        topic_id=None,
        thread_ts=None,
        connection_id=None,
        owner_user_id=None,
        workspace_id="W123",
        metadata={},
        files=[],
    )


class TestThreadResolverLookup:
    """Tests for thread ID lookup."""

    @pytest.mark.asyncio
    async def test_lookup_thread_id_from_store(self, thread_resolver, mock_store, inbound_message):
        """Should look up thread ID from store when no connection repo."""
        thread_resolver._connection_repo = None
        mock_store.get_thread_id.return_value = "thread-123"

        result = await thread_resolver.lookup_thread_id(inbound_message)

        assert result == "thread-123"
        mock_store.get_thread_id.assert_called_once_with("slack", "C123", topic_id=None)

    @pytest.mark.asyncio
    async def test_lookup_thread_id_from_connection_repo(self, thread_resolver, mock_connection_repo, inbound_message):
        """Should look up thread ID from connection repo when available."""
        inbound_message.connection_id = "conn-123"
        mock_connection_repo.get_thread_id.return_value = "thread-456"

        result = await thread_resolver.lookup_thread_id(inbound_message)

        assert result == "thread-456"
        mock_connection_repo.get_thread_id.assert_called_once_with("conn-123", "C123", None)

    @pytest.mark.asyncio
    async def test_lookup_thread_id_returns_none(self, thread_resolver, mock_store, inbound_message):
        """Should return None when no thread found."""
        mock_store.get_thread_id.return_value = None

        result = await thread_resolver.lookup_thread_id(inbound_message)

        assert result is None


class TestThreadResolverStore:
    """Tests for thread ID storage."""

    @pytest.mark.asyncio
    async def test_store_thread_id_in_store(self, thread_resolver, mock_store, inbound_message):
        """Should store thread ID in store when no connection repo."""
        thread_resolver._connection_repo = None

        await thread_resolver.store_thread_id(inbound_message, "thread-123")

        mock_store.set_thread_id.assert_called_once_with("slack", "C123", "thread-123", topic_id=None, user_id="U123")

    @pytest.mark.asyncio
    async def test_store_thread_id_in_connection_repo(self, thread_resolver, mock_connection_repo, inbound_message):
        """Should store thread ID in connection repo when available."""
        inbound_message.connection_id = "conn-123"
        inbound_message.owner_user_id = "owner-123"

        await thread_resolver.store_thread_id(inbound_message, "thread-123")

        mock_connection_repo.set_thread_id.assert_called_once_with(
            connection_id="conn-123",
            owner_user_id="owner-123",
            provider="slack",
            external_conversation_id="C123",
            external_topic_id=None,
            thread_id="thread-123",
        )


class TestThreadResolverCreate:
    """Tests for thread creation."""

    @pytest.mark.asyncio
    async def test_create_thread(self, thread_resolver, inbound_message):
        """Should create a new thread via client."""
        mock_client = AsyncMock()
        mock_client.threads.create.return_value = {"thread_id": "new-thread-123"}

        with patch("app.channels.manager._thread_channel_metadata") as mock_metadata, patch("app.channels.manager._owner_headers") as mock_headers:
            mock_metadata.return_value = {"channel_source": {"type": "im_channel"}}
            mock_headers.return_value = None

            result = await thread_resolver.create_thread(mock_client, inbound_message)

            assert result == "new-thread-123"
            mock_client.threads.create.assert_called_once()


class TestThreadResolverResolveOrCreate:
    """Tests for the main resolve_or_create_thread method."""

    @pytest.mark.asyncio
    async def test_resolve_existing_thread(self, thread_resolver, mock_store, inbound_message):
        """Should return existing thread ID."""
        mock_store.get_thread_id.return_value = "existing-thread"
        mock_client = AsyncMock()

        with patch.object(thread_resolver, "update_thread_channel_metadata") as mock_update:
            result = await thread_resolver.resolve_or_create_thread(mock_client, inbound_message)

            assert result == "existing-thread"
            mock_update.assert_called_once_with(mock_client, inbound_message, "existing-thread")

    @pytest.mark.asyncio
    async def test_create_new_thread_when_not_found(self, thread_resolver, mock_store, inbound_message):
        """Should create new thread when not found."""
        mock_store.get_thread_id.return_value = None
        mock_client = AsyncMock()

        with patch.object(thread_resolver, "create_thread") as mock_create:
            mock_create.return_value = "new-thread"
            result = await thread_resolver.resolve_or_create_thread(mock_client, inbound_message)

            assert result == "new-thread"
            mock_create.assert_called_once_with(mock_client, inbound_message)
