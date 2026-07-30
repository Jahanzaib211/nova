"""Stream bridge — decouples agent workers from SSE endpoints.

A ``StreamBridge`` sits between the background task that runs an agent
(producer) and the HTTP endpoint that pushes Server-Sent Events to
the client (consumer). This package provides an abstract protocol
(:class:`StreamBridge`) plus two implementations: :class:`MemoryStreamBridge`
(an in-process event log — single replica only) and
:class:`RedisStreamBridge` (Redis Streams — required for a multi-replica
Gateway, see ``k8s/ARCHITECTURE.md``).
"""

from .async_provider import make_stream_bridge
from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent
from .memory import MemoryStreamBridge
from .redis_provider import RedisStreamBridge

__all__ = [
    "END_SENTINEL",
    "HEARTBEAT_SENTINEL",
    "MemoryStreamBridge",
    "RedisStreamBridge",
    "StreamBridge",
    "StreamEvent",
    "make_stream_bridge",
]
