"""Event publisher — convenience wrapper for publishing events.

Phase C3 — thin wrapper that injects correlation_id and timestamp
automatically.  Used by service implementations to emit events
without manually constructing every field.
"""

from __future__ import annotations

from typing import Any

from deerflow.events.bus import EventBus, event_bus
from deerflow.events.event import DomainEvent


class EventPublisher:
    """Thin wrapper for publishing events with automatic metadata.

    Injects correlation_id, run_id, thread_id, and timestamp
    automatically from the provided context.
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus or event_bus

    def publish(
        self,
        event_class: type[DomainEvent],
        *,
        correlation_id: str = "",
        run_id: str = "",
        thread_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        """Create and publish an event with automatic metadata.

        Returns the published event for inspection/testing.
        """
        event = event_class(
            correlation_id=correlation_id,
            run_id=run_id,
            thread_id=thread_id,
            payload=payload or {},
        )
        self._bus.publish(event)
        return event
