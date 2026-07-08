"""ThreadResolver service for channel thread management.

This module provides the ThreadResolver service that handles thread creation,
lookup, and metadata management for IM channels.
"""

from __future__ import annotations

import logging
from typing import Any

from app.channels.message_bus import InboundMessage

logger = logging.getLogger(__name__)


class ThreadResolver:
    """Service for resolving and managing IM channel threads.

    This service encapsulates all thread-related operations:
    - Thread ID lookup (from store or connection repo)
    - Thread creation via Gateway
    - Thread metadata updates
    - Thread ID storage
    """

    def __init__(
        self,
        store: Any,
        connection_repo: Any | None = None,
    ) -> None:
        """Initialize the ThreadResolver.

        Args:
            store: ChannelStore instance for thread ID persistence.
            connection_repo: Optional connection repository for bound identity threads.
        """
        self._store = store
        self._connection_repo = connection_repo
        self._channel_metadata_synced: set[str] = set()

    async def lookup_thread_id(self, msg: InboundMessage) -> str | None:
        """Look up existing thread ID for a message.

        Args:
            msg: The inbound message to look up.

        Returns:
            The thread ID if found, None otherwise.
        """
        if msg.connection_id and self._connection_repo is not None:
            return await self._connection_repo.get_thread_id(
                msg.connection_id,
                msg.chat_id,
                msg.topic_id,
            )
        return self._store.get_thread_id(msg.channel_name, msg.chat_id, topic_id=msg.topic_id)

    async def store_thread_id(self, msg: InboundMessage, thread_id: str) -> None:
        """Store thread ID for a message.

        Args:
            msg: The inbound message.
            thread_id: The thread ID to store.
        """
        if msg.connection_id and msg.owner_user_id and self._connection_repo is not None:
            await self._connection_repo.set_thread_id(
                connection_id=msg.connection_id,
                owner_user_id=msg.owner_user_id,
                provider=msg.channel_name,
                external_conversation_id=msg.chat_id,
                external_topic_id=msg.topic_id,
                thread_id=thread_id,
            )
            return

        self._store.set_thread_id(
            msg.channel_name,
            msg.chat_id,
            thread_id,
            topic_id=msg.topic_id,
            user_id=msg.user_id,
        )

    async def create_thread(self, client: Any, msg: InboundMessage) -> str:
        """Create a new thread through Gateway and store the mapping.

        Args:
            client: LangGraph SDK async client.
            msg: The inbound message.

        Returns:
            The newly created thread ID.
        """
        from app.channels.manager import _owner_headers, _thread_channel_metadata

        metadata = _thread_channel_metadata(msg)
        owner_headers = _owner_headers(msg)
        if owner_headers:
            thread = await client.threads.create(metadata=metadata, headers=owner_headers)
        else:
            thread = await client.threads.create(metadata=metadata)
        thread_id = thread["thread_id"]
        await self.store_thread_id(msg, thread_id)
        logger.info(
            "[ThreadResolver] new thread created: thread_id=%s for chat_id=%s topic_id=%s",
            thread_id,
            msg.chat_id,
            msg.topic_id,
        )
        return thread_id

    async def update_thread_channel_metadata(
        self,
        client: Any,
        msg: InboundMessage,
        thread_id: str,
    ) -> None:
        """Best-effort source metadata backfill for existing IM-created threads.

        Args:
            client: LangGraph SDK async client.
            msg: The inbound message.
            thread_id: The thread ID to update.
        """
        from app.channels.manager import _owner_headers, _thread_channel_metadata

        # The metadata (provider/chat/topic) is constant for a thread, so one
        # successful backfill per manager lifetime is enough — skip the
        # redundant PATCH on every subsequent inbound message.
        if thread_id in self._channel_metadata_synced:
            return
        update_kwargs: dict[str, Any] = {"metadata": _thread_channel_metadata(msg)}
        if owner_headers := _owner_headers(msg):
            update_kwargs["headers"] = owner_headers
        try:
            await client.threads.update(thread_id, **update_kwargs)
        except Exception:
            logger.debug(
                "[ThreadResolver] failed to update channel metadata for thread_id=%s",
                thread_id,
                exc_info=True,
            )
            return
        if len(self._channel_metadata_synced) > 4096:
            self._channel_metadata_synced.clear()
        self._channel_metadata_synced.add(thread_id)

    async def resolve_or_create_thread(
        self,
        client: Any,
        msg: InboundMessage,
    ) -> str:
        """Resolve existing thread or create a new one.

        This is the main entry point for thread resolution. It looks up
        an existing thread, and if not found, creates a new one.

        Args:
            client: LangGraph SDK async client.
            msg: The inbound message.

        Returns:
            The thread ID (either existing or newly created).
        """
        thread_id = await self.lookup_thread_id(msg)
        if thread_id:
            logger.info(
                "[ThreadResolver] reusing thread: thread_id=%s for topic_id=%s",
                thread_id,
                msg.topic_id,
            )
            await self.update_thread_channel_metadata(client, msg, thread_id)
        else:
            thread_id = await self.create_thread(client, msg)
        return thread_id
