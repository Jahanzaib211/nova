"""Event subscriber — convenience decorator for event handlers.

Phase C3 — decorator-based subscriber registration that works with
the EventBus.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from deerflow.events.bus import EventBus, event_bus
from deerflow.events.event import DomainEvent

E = TypeVar("E", bound=DomainEvent)


class EventSubscriber:
    """Decorator-based event subscriber registration.

    Usage::

        subscriber = EventSubscriber()

        @subscriber.on(RunCreated)
        def handle_run_created(event: RunCreated) -> None:
            print(f"Run {event.run_id} created")

        # Register all decorated handlers with a bus
        subscriber.register(event_bus)
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[type[DomainEvent], Callable[[DomainEvent], None]]] = []

    def on(self, event_type: type[E]) -> Callable[[Callable[[E], None]], Callable[[E], None]]:
        """Decorator to register a handler for an event type.

        Usage::

            @subscriber.on(RunCreated)
            def handle(event: RunCreated) -> None:
                ...
        """
        def decorator(fn: Callable[[E], None]) -> Callable[[E], None]:
            self._handlers.append((event_type, fn))  # type: ignore[arg-type]
            return fn
        return decorator

    def register(self, bus: EventBus | None = None) -> None:
        """Register all decorated handlers with an event bus."""
        target_bus = bus or event_bus
        for event_type, handler in self._handlers:
            target_bus.subscribe(event_type, handler)
