"""Event registry — tracks event types and their metadata.

Phase C3 — central registry for event type discovery and validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deerflow.events.event import DomainEvent


@dataclass
class EventMetadata:
    """Metadata for a registered event type."""

    event_type: type[DomainEvent]
    description: str = ""
    category: str = "general"


class EventRegistry:
    """Central registry for event type discovery.

    Allows runtime introspection of available event types and their
    metadata.  Used by diagnostics, logging, and documentation tools.
    """

    def __init__(self) -> None:
        self._events: dict[str, EventMetadata] = {}

    def register(
        self,
        event_type: type[DomainEvent],
        *,
        description: str = "",
        category: str = "general",
    ) -> None:
        """Register an event type with metadata."""
        self._events[event_type.__name__] = EventMetadata(
            event_type=event_type,
            description=description,
            category=category,
        )

    def get(self, name: str) -> EventMetadata | None:
        """Look up an event type by name."""
        return self._events.get(name)

    def list_events(self) -> list[str]:
        """Return all registered event type names."""
        return list(self._events.keys())

    def list_by_category(self, category: str) -> list[str]:
        """Return event type names for a given category."""
        return [
            name for name, meta in self._events.items()
            if meta.category == category
        ]


# Module-level registry with all C3 events pre-registered
event_registry = EventRegistry()

# Auto-register all event types from the event module
from deerflow.events import event as _event_module

for _name in dir(_event_module):
    _cls = getattr(_event_module, _name)
    if (
        isinstance(_cls, type)
        and issubclass(_cls, DomainEvent)
        and _cls is not DomainEvent
    ):
        _category = "lifecycle"
        if "Workspace" in _name:
            _category = "workspace"
        elif "Browser" in _name:
            _category = "browser"
        elif "Health" in _name:
            _category = "health"
        elif "Recovery" in _name:
            _category = "recovery"
        elif "Execution" in _name or "Process" in _name:
            _category = "execution"
        elif "Tool" in _name:
            _category = "tool"
        elif "Artifact" in _name:
            _category = "artifact"
        event_registry.register(_cls, category=_category)
