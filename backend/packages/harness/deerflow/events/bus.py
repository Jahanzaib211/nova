"""Typed domain event bus for Nova.

Phase C3 — synchronous, deterministic event delivery with subscriber
management.  The bus is dependency-injection compatible (no module globals
in the bus itself; the module-level ``event_bus`` singleton is opt-in).

Usage::

    from deerflow.events.bus import EventBus
    from deerflow.events.event import RunCreated

    bus = EventBus()

    def on_run_created(event: RunCreated) -> None:
        print(f"Run {event.run_id} created")

    bus.subscribe(RunCreated, on_run_created)
    bus.publish(RunCreated(run_id="abc", thread_id="t1"))
    # on_run_created was called synchronously
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from deerflow.events.event import DomainEvent

logger = logging.getLogger(__name__)

# Type alias for event handler functions
EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """Synchronous typed event bus.

    Features:
        - Typed subscribe/publish by event class
        - Deterministic ordering (handlers called in registration order)
        - Synchronous delivery (no async, no threads)
        - Subscriber removal
        - Event replay from history
        - Dependency-injection compatible (no module globals)

    Design:
        - No module globals in the bus itself
        - The module-level ``event_bus`` is a convenience singleton
        - For testing, create a fresh EventBus and inspect its history
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)
        self._history: list[DomainEvent] = []
        self._max_history: int = 10_000

    def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: EventHandler,
    ) -> None:
        """Register a handler for a specific event type.

        The same handler can be registered multiple times; it will be
        called once per registration.
        """
        self._handlers[event_type].append(handler)
        logger.debug(
            "Subscribed %s to %s",
            getattr(handler, "__name__", repr(handler)),
            event_type.__name__,
        )

    def unsubscribe(
        self,
        event_type: type[DomainEvent],
        handler: EventHandler,
    ) -> bool:
        """Remove a handler for a specific event type.

        Returns True if the handler was found and removed, False otherwise.
        """
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
            logger.debug(
                "Unsubscribed %s from %s",
                getattr(handler, "__name__", repr(handler)),
                event_type.__name__,
            )
            return True
        except ValueError:
            return False

    def publish(self, event: DomainEvent) -> None:
        """Publish an event to all registered handlers.

        Handlers are called synchronously in registration order.
        Exceptions in handlers are logged but do not prevent other
        handlers from being called.
        """
        # Record in history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        # Also notify handlers registered for the base DomainEvent class
        if event_type is not DomainEvent:
            base_handlers = self._handlers.get(DomainEvent, [])
            handlers = handlers + base_handlers

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Handler %s failed for event %s",
                    getattr(handler, "__name__", repr(handler)),
                    event.event_type,
                )

    def replay(
        self,
        event_type: type[DomainEvent] | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]:
        """Replay events from history.

        If event_type is None, returns all events (up to limit).
        If event_type is specified, returns only matching events.
        """
        if event_type is None:
            return list(self._history[-limit:])
        return [e for e in self._history if isinstance(e, event_type)][-limit:]

    def clear_history(self) -> None:
        """Clear the event history."""
        self._history.clear()

    @property
    def history_size(self) -> int:
        """Return the number of events in history."""
        return len(self._history)

    def handler_count(self, event_type: type[DomainEvent]) -> int:
        """Return the number of handlers registered for an event type."""
        return len(self._handlers.get(event_type, []))


# ---------------------------------------------------------------------------
# Module-level convenience singleton (opt-in)
# ---------------------------------------------------------------------------

event_bus = EventBus()
