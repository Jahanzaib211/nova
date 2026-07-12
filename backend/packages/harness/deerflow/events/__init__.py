"""Nova domain event system.

Phase C3 — typed, synchronous event bus with domain events.

Usage::

    from deerflow.events import event_bus, EventPublisher, EventSubscriber
    from deerflow.events.event import RunCreated

    # Publish an event
    publisher = EventPublisher()
    publisher.publish(RunCreated, run_id="abc", thread_id="t1")

    # Subscribe to events
    subscriber = EventSubscriber()

    @subscriber.on(RunCreated)
    def handle(event: RunCreated) -> None:
        print(f"Run {event.run_id} created")

    subscriber.register(event_bus)

    # Or use the bus directly
    event_bus.subscribe(RunCreated, handle)
    event_bus.publish(RunCreated(run_id="abc", thread_id="t1"))
"""

from deerflow.events.bus import EventBus, event_bus
from deerflow.events.event import (
    ArtifactCreated,
    BrowserStarted,
    BrowserStopped,
    DomainEvent,
    HealthChanged,
    RunCancelled,
    RunCheckpointCreated,
    RunCompleted,
    RunCreated,
    RunFailed,
    RunInitialized,
    RunArchived,
    RunPaused,
    RunRecovering,
    RunResumed,
    RunStarted,
    ToolExecuted,
    WorkspaceMounted,
    WorkspaceReleased,
)
from deerflow.events.publisher import EventPublisher
from deerflow.events.registry import EventRegistry, event_registry
from deerflow.events.subscriber import EventSubscriber

__all__ = [
    # Bus
    "EventBus",
    "event_bus",
    # Publisher
    "EventPublisher",
    # Subscriber
    "EventSubscriber",
    # Registry
    "EventRegistry",
    "event_registry",
    # Base event
    "DomainEvent",
    # Lifecycle events
    "RunCreated",
    "RunInitialized",
    "RunStarted",
    "RunCheckpointCreated",
    "RunPaused",
    "RunResumed",
    "RunRecovering",
    "RunCompleted",
    "RunFailed",
    "RunCancelled",
    "RunArchived",
    # Workspace events
    "WorkspaceMounted",
    "WorkspaceReleased",
    # Browser events
    "BrowserStarted",
    "BrowserStopped",
    # Health events
    "HealthChanged",
    # Tool events
    "ToolExecuted",
    # Artifact events
    "ArtifactCreated",
]
