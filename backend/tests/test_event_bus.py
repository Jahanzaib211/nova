"""Tests for Phase C3 — lifecycle model, event bus, and integration.

Covers:
- RunLifecycleStatus enum: states, properties, adapters
- EventBus: subscribe, publish, unsubscribe, replay, history
- EventPublisher: automatic metadata injection
- EventSubscriber: decorator-based registration
- EventRegistry: event type discovery
- Domain events: creation, immutability, event_type property
- RunServiceImpl integration: lifecycle events on state transitions
- DiagnosticsServiceImpl integration: subscribes to EventBus
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =====================================================================
# Lifecycle model
# =====================================================================


class TestRunLifecycleStatus:
    """Verify RunLifecycleStatus enum states and properties."""

    def test_has_all_states(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus

        expected = {
            "created", "initializing", "running", "checkpoint",
            "paused", "resumed", "recovering", "completed",
            "failed", "cancelled", "archived",
        }
        actual = {s.value for s in RunLifecycleStatus}
        assert actual == expected

    def test_terminal_states(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus

        assert RunLifecycleStatus.COMPLETED.is_terminal
        assert RunLifecycleStatus.FAILED.is_terminal
        assert RunLifecycleStatus.CANCELLED.is_terminal
        assert RunLifecycleStatus.ARCHIVED.is_terminal
        assert not RunLifecycleStatus.RUNNING.is_terminal
        assert not RunLifecycleStatus.CREATED.is_terminal

    def test_active_states(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus

        assert RunLifecycleStatus.RUNNING.is_active
        assert RunLifecycleStatus.INITIALIZING.is_active
        assert RunLifecycleStatus.RECOVERING.is_active
        assert not RunLifecycleStatus.COMPLETED.is_active
        assert not RunLifecycleStatus.PAUSED.is_active

    def test_transitional_states(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus

        assert RunLifecycleStatus.CHECKPOINT.is_transitional
        assert RunLifecycleStatus.PAUSED.is_transitional
        assert RunLifecycleStatus.RESUMED.is_transitional
        assert not RunLifecycleStatus.RUNNING.is_transitional

    def test_is_str_enum(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus

        assert isinstance(RunLifecycleStatus.RUNNING, str)
        assert RunLifecycleStatus.RUNNING == "running"


class TestAdaptRunStatus:
    """Verify adapters between existing enums and RunLifecycleStatus."""

    def test_adapt_pending_to_created(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("pending") == RunLifecycleStatus.CREATED

    def test_adapt_running_to_running(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("running") == RunLifecycleStatus.RUNNING

    def test_adapt_success_to_completed(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("success") == RunLifecycleStatus.COMPLETED

    def test_adapt_error_to_failed(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("error") == RunLifecycleStatus.FAILED

    def test_adapt_timeout_to_failed(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("timeout") == RunLifecycleStatus.FAILED

    def test_adapt_interrupted_to_cancelled(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("interrupted") == RunLifecycleStatus.CANCELLED

    def test_adapt_enum_passthrough(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status(RunLifecycleStatus.RUNNING) == RunLifecycleStatus.RUNNING

    def test_adapt_unknown_defaults_to_running(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

        assert adapt_run_status("bogus") == RunLifecycleStatus.RUNNING


class TestToRunStatus:
    """Verify conversion back to legacy RunStatus strings."""

    def test_completed_to_success(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, to_run_status

        assert to_run_status(RunLifecycleStatus.COMPLETED) == "success"

    def test_failed_to_error(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, to_run_status

        assert to_run_status(RunLifecycleStatus.FAILED) == "error"

    def test_cancelled_to_interrupted(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, to_run_status

        assert to_run_status(RunLifecycleStatus.CANCELLED) == "interrupted"

    def test_running_to_running(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, to_run_status

        assert to_run_status(RunLifecycleStatus.RUNNING) == "running"

    def test_created_to_pending(self):
        from deerflow.runtime.lifecycle import RunLifecycleStatus, to_run_status

        assert to_run_status(RunLifecycleStatus.CREATED) == "pending"


# =====================================================================
# Domain events
# =====================================================================


class TestDomainEvent:
    """Verify domain event creation and immutability."""

    def test_base_event_creation(self):
        from deerflow.events.event import DomainEvent

        event = DomainEvent(run_id="r1", thread_id="t1", correlation_id="c1")
        assert event.run_id == "r1"
        assert event.thread_id == "t1"
        assert event.correlation_id == "c1"
        assert event.event_type == "DomainEvent"
        assert isinstance(event.event_id, str)
        assert len(event.event_id) == 32  # uuid hex
        assert event.timestamp  # non-empty

    def test_event_is_frozen(self):
        from deerflow.events.event import DomainEvent

        event = DomainEvent(run_id="r1")
        with pytest.raises(AttributeError):
            event.run_id = "r2"  # type: ignore[misc]

    def test_event_payload(self):
        from deerflow.events.event import DomainEvent

        event = DomainEvent(payload={"key": "value"})
        assert event.payload == {"key": "value"}

    def test_lifecycle_event_type_names(self):
        from deerflow.events.event import (
            RunArchived,
            RunCancelled,
            RunCompleted,
            RunCreated,
            RunFailed,
            RunInitialized,
            RunPaused,
            RunRecovering,
            RunResumed,
            RunStarted,
        )

        assert RunCreated().event_type == "RunCreated"
        assert RunStarted().event_type == "RunStarted"
        assert RunCompleted().event_type == "RunCompleted"
        assert RunFailed().event_type == "RunFailed"
        assert RunCancelled().event_type == "RunCancelled"
        assert RunArchived().event_type == "RunArchived"
        assert RunPaused().event_type == "RunPaused"
        assert RunResumed().event_type == "RunResumed"
        assert RunRecovering().event_type == "RunRecovering"
        assert RunInitialized().event_type == "RunInitialized"

    def test_non_lifecycle_events(self):
        from deerflow.events.event import (
            ArtifactCreated,
            BrowserStarted,
            BrowserStopped,
            HealthChanged,
            ToolExecuted,
            WorkspaceMounted,
            WorkspaceReleased,
        )

        assert ArtifactCreated().event_type == "ArtifactCreated"
        assert BrowserStarted().event_type == "BrowserStarted"
        assert BrowserStopped().event_type == "BrowserStopped"
        assert HealthChanged().event_type == "HealthChanged"
        assert ToolExecuted().event_type == "ToolExecuted"
        assert WorkspaceMounted().event_type == "WorkspaceMounted"
        assert WorkspaceReleased().event_type == "WorkspaceReleased"


# =====================================================================
# Event bus
# =====================================================================


class TestEventBus:
    """Verify EventBus subscribe, publish, unsubscribe, replay."""

    def setup_method(self):
        from deerflow.events.bus import EventBus

        self.bus = EventBus()

    def test_subscribe_and_publish(self):
        from deerflow.events.event import RunCreated

        received = []
        self.bus.subscribe(RunCreated, lambda e: received.append(e))

        event = RunCreated(run_id="r1", thread_id="t1")
        self.bus.publish(event)

        assert len(received) == 1
        assert received[0].run_id == "r1"

    def test_multiple_handlers(self):
        from deerflow.events.event import RunCreated

        results_a = []
        results_b = []
        self.bus.subscribe(RunCreated, lambda e: results_a.append("a"))
        self.bus.subscribe(RunCreated, lambda e: results_b.append("b"))

        self.bus.publish(RunCreated(run_id="r1"))

        assert results_a == ["a"]
        assert results_b == ["b"]

    def test_unsubscribe(self):
        from deerflow.events.event import RunCreated

        received = []
        handler = lambda e: received.append(e)
        self.bus.subscribe(RunCreated, handler)

        self.bus.publish(RunCreated(run_id="r1"))
        assert len(received) == 1

        removed = self.bus.unsubscribe(RunCreated, handler)
        assert removed is True

        self.bus.publish(RunCreated(run_id="r2"))
        assert len(received) == 1  # not called again

    def test_unsubscribe_nonexistent(self):
        from deerflow.events.event import RunCreated

        removed = self.bus.unsubscribe(RunCreated, lambda e: None)
        assert removed is False

    def test_publish_records_history(self):
        from deerflow.events.event import RunCompleted, RunCreated

        self.bus.publish(RunCreated(run_id="r1"))
        self.bus.publish(RunCompleted(run_id="r1"))

        assert self.bus.history_size == 2

    def test_replay_all(self):
        from deerflow.events.event import RunCompleted, RunCreated

        self.bus.publish(RunCreated(run_id="r1"))
        self.bus.publish(RunCompleted(run_id="r1"))

        history = self.bus.replay()
        assert len(history) == 2

    def test_replay_filtered(self):
        from deerflow.events.event import RunCompleted, RunCreated

        self.bus.publish(RunCreated(run_id="r1"))
        self.bus.publish(RunCompleted(run_id="r1"))
        self.bus.publish(RunCreated(run_id="r2"))

        created = self.bus.replay(event_type=RunCreated)
        assert len(created) == 2

    def test_replay_with_limit(self):
        from deerflow.events.event import RunCreated

        for i in range(5):
            self.bus.publish(RunCreated(run_id=f"r{i}"))

        history = self.bus.replay(limit=3)
        assert len(history) == 3

    def test_clear_history(self):
        from deerflow.events.event import RunCreated

        self.bus.publish(RunCreated(run_id="r1"))
        self.bus.clear_history()
        assert self.bus.history_size == 0

    def test_handler_exception_does_not_break_other_handlers(self):
        from deerflow.events.event import RunCreated

        def bad_handler(e):
            raise ValueError("boom")

        good_results = []
        self.bus.subscribe(RunCreated, bad_handler)
        self.bus.subscribe(RunCreated, lambda e: good_results.append("ok"))

        self.bus.publish(RunCreated(run_id="r1"))
        assert good_results == ["ok"]

    def test_handler_count(self):
        from deerflow.events.event import RunCreated

        self.bus.subscribe(RunCreated, lambda e: None)
        self.bus.subscribe(RunCreated, lambda e: None)
        assert self.bus.handler_count(RunCreated) == 2

    def test_base_domain_event_handler_gets_all_events(self):
        from deerflow.events.event import DomainEvent, RunCompleted, RunCreated

        received = []
        self.bus.subscribe(DomainEvent, lambda e: received.append(e.event_type))

        self.bus.publish(RunCreated(run_id="r1"))
        self.bus.publish(RunCompleted(run_id="r1"))

        assert received == ["RunCreated", "RunCompleted"]

    def test_history_max_size(self):
        from deerflow.events.event import RunCreated

        bus = self.bus
        bus._max_history = 5
        for i in range(10):
            bus.publish(RunCreated(run_id=f"r{i}"))
        assert bus.history_size == 5


# =====================================================================
# Event publisher
# =====================================================================


class TestEventPublisher:
    """Verify EventPublisher automatic metadata injection."""

    def test_publish_injects_metadata(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCreated
        from deerflow.events.publisher import EventPublisher

        bus = EventBus()
        publisher = EventPublisher(bus)

        received = []
        bus.subscribe(RunCreated, lambda e: received.append(e))

        event = publisher.publish(
            RunCreated,
            correlation_id="corr1",
            run_id="run1",
            thread_id="thread1",
            payload={"model": "gpt-4"},
        )

        assert len(received) == 1
        assert received[0].correlation_id == "corr1"
        assert received[0].run_id == "run1"
        assert received[0].thread_id == "thread1"
        assert received[0].payload == {"model": "gpt-4"}
        assert event is received[0]

    def test_publish_uses_singleton_bus_by_default(self):
        from deerflow.events.bus import event_bus
        from deerflow.events.event import RunCreated
        from deerflow.events.publisher import EventPublisher

        publisher = EventPublisher()
        # Should use the module-level event_bus (not crash)
        event = publisher.publish(RunCreated, run_id="test")
        assert event.run_id == "test"
        # Clean up
        event_bus.clear_history()


# =====================================================================
# Event subscriber
# =====================================================================


class TestEventSubscriber:
    """Verify EventSubscriber decorator-based registration."""

    def test_decorator_registration(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCreated
        from deerflow.events.subscriber import EventSubscriber

        bus = EventBus()
        subscriber = EventSubscriber()
        received = []

        @subscriber.on(RunCreated)
        def handle(event: RunCreated) -> None:
            received.append(event.run_id)

        subscriber.register(bus)
        bus.publish(RunCreated(run_id="r1"))

        assert received == ["r1"]

    def test_multiple_decorators(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCompleted, RunCreated
        from deerflow.events.subscriber import EventSubscriber

        bus = EventBus()
        subscriber = EventSubscriber()
        events = []

        @subscriber.on(RunCreated)
        def on_created(e: RunCreated) -> None:
            events.append("created")

        @subscriber.on(RunCompleted)
        def on_completed(e: RunCompleted) -> None:
            events.append("completed")

        subscriber.register(bus)
        bus.publish(RunCreated(run_id="r1"))
        bus.publish(RunCompleted(run_id="r1"))

        assert events == ["created", "completed"]


# =====================================================================
# Event registry
# =====================================================================


class TestEventRegistry:
    """Verify EventRegistry event type discovery."""

    def test_all_lifecycle_events_registered(self):
        from deerflow.events.registry import event_registry

        lifecycle = event_registry.list_by_category("lifecycle")
        assert "RunCreated" in lifecycle
        assert "RunStarted" in lifecycle
        assert "RunCompleted" in lifecycle
        assert "RunFailed" in lifecycle
        assert "RunCancelled" in lifecycle
        assert "RunArchived" in lifecycle

    def test_workspace_events_registered(self):
        from deerflow.events.registry import event_registry

        workspace = event_registry.list_by_category("workspace")
        assert "WorkspaceMounted" in workspace
        assert "WorkspaceReleased" in workspace

    def test_browser_events_registered(self):
        from deerflow.events.registry import event_registry

        browser = event_registry.list_by_category("browser")
        assert "BrowserStarted" in browser
        assert "BrowserStopped" in browser

    def test_health_events_registered(self):
        from deerflow.events.registry import event_registry

        health = event_registry.list_by_category("health")
        assert "HealthChanged" in health

    def test_get_event_metadata(self):
        from deerflow.events.registry import event_registry

        meta = event_registry.get("RunCreated")
        assert meta is not None
        assert meta.category == "lifecycle"

    def test_list_events(self):
        from deerflow.events.registry import event_registry

        all_events = event_registry.list_events()
        assert len(all_events) >= 17  # at least 17 events registered


# =====================================================================
# RunServiceImpl lifecycle event integration
# =====================================================================


class TestRunServiceImplLifecycleEvents:
    """Verify RunServiceImpl publishes lifecycle events on state transitions."""

    @pytest.mark.asyncio
    async def test_create_publishes_run_created(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCreated
        from deerflow.services.implementations import RunServiceImpl

        bus = EventBus()
        mock_manager = AsyncMock()
        mock_record = MagicMock()
        mock_record.run_id = "run-1"
        mock_record.thread_id = "thread-1"
        mock_record.assistant_id = "lead-agent"
        mock_record.status.value = "pending"
        mock_record.correlation_id = "corr-1"
        mock_record.model_name = "gpt-4"
        mock_record.created_at = "2026-01-01T00:00:00"
        mock_record.error = None
        mock_record.total_tokens = 0
        mock_record.message_count = 0
        mock_manager.create.return_value = mock_record

        impl = RunServiceImpl(mock_manager)
        impl._event_bus = bus

        received = []
        bus.subscribe(RunCreated, lambda e: received.append(e))

        result = await impl.create("thread-1")

        assert len(received) == 1
        assert received[0].run_id == "run-1"
        assert received[0].correlation_id == "corr-1"
        assert received[0].thread_id == "thread-1"
        assert result.run_id == "run-1"

    @pytest.mark.asyncio
    async def test_cancel_publishes_run_cancelled(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCancelled
        from deerflow.services.implementations import RunServiceImpl

        bus = EventBus()
        mock_manager = AsyncMock()
        mock_manager.cancel.return_value = True

        impl = RunServiceImpl(mock_manager)
        impl._event_bus = bus

        received = []
        bus.subscribe(RunCancelled, lambda e: received.append(e))

        result = await impl.cancel("run-1", action="interrupt")

        assert result is True
        assert len(received) == 1
        assert received[0].run_id == "run-1"

    @pytest.mark.asyncio
    async def test_set_status_publishes_lifecycle_event(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCompleted, RunFailed
        from deerflow.services.implementations import RunServiceImpl

        bus = EventBus()
        mock_manager = AsyncMock()

        impl = RunServiceImpl(mock_manager)
        impl._event_bus = bus

        completed_received = []
        failed_received = []
        bus.subscribe(RunCompleted, lambda e: completed_received.append(e))
        bus.subscribe(RunFailed, lambda e: failed_received.append(e))

        await impl.set_status("run-1", "success")
        assert len(completed_received) == 1
        assert completed_received[0].run_id == "run-1"

        await impl.set_status("run-1", "error", error="boom")
        assert len(failed_received) == 1
        assert failed_received[0].payload.get("error") == "boom"


# =====================================================================
# DiagnosticsServiceImpl event bus integration
# =====================================================================


class TestDiagnosticsServiceEventBusIntegration:
    """Verify DiagnosticsService subscribes to EventBus and records events."""

    @patch("deerflow.services.implementations.DiagnosticsServiceImpl.record")
    def test_subscribes_to_all_events(self, mock_record):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCreated
        from deerflow.services.implementations import DiagnosticsServiceImpl

        bus = EventBus()
        diag = DiagnosticsServiceImpl()
        diag.subscribe_to_events(bus)

        bus.publish(RunCreated(run_id="r1", thread_id="t1", correlation_id="c1"))

        mock_record.assert_called_once()
        call_args = mock_record.call_args
        assert call_args[0][0] == "event:RunCreated"
        assert call_args[1]["run_id"] == "r1"
        assert call_args[1]["correlation_id"] == "c1"

    @patch("deerflow.services.implementations.DiagnosticsServiceImpl.record")
    def test_records_multiple_event_types(self, mock_record):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import RunCompleted, RunCreated
        from deerflow.services.implementations import DiagnosticsServiceImpl

        bus = EventBus()
        diag = DiagnosticsServiceImpl()
        diag.subscribe_to_events(bus)

        bus.publish(RunCreated(run_id="r1"))
        bus.publish(RunCompleted(run_id="r1"))

        assert mock_record.call_count == 2
        stages = [call[0][0] for call in mock_record.call_args_list]
        assert "event:RunCreated" in stages
        assert "event:RunCompleted" in stages


# =====================================================================
# HealthServiceImpl HealthChanged event integration
# =====================================================================


class TestHealthServiceImplHealthChangedEvents:
    """Verify HealthServiceImpl publishes HealthChanged on state transitions."""

    @pytest.mark.asyncio
    async def test_publishes_health_changed_on_transition(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import HealthChanged
        from deerflow.services.implementations import HealthServiceImpl

        bus = EventBus()
        healthy_probe = AsyncMock(return_value=MagicMock(healthy=True, message="ok"))
        impl = HealthServiceImpl(probes={"p1": healthy_probe}, event_bus=bus)

        received = []
        bus.subscribe(HealthChanged, lambda e: received.append(e))

        await impl.check_all()
        assert len(received) == 1
        assert received[0].payload["healthy"] is True

    @pytest.mark.asyncio
    async def test_no_event_if_state_unchanged(self):
        from deerflow.events.bus import EventBus
        from deerflow.events.event import HealthChanged
        from deerflow.services.implementations import HealthServiceImpl

        bus = EventBus()
        healthy_probe = AsyncMock(return_value=MagicMock(healthy=True, message="ok"))
        impl = HealthServiceImpl(probes={"p1": healthy_probe}, event_bus=bus)

        received = []
        bus.subscribe(HealthChanged, lambda e: received.append(e))

        await impl.check_all()
        await impl.check_all()  # second call, same state
        assert len(received) == 1  # only first call emits


# =====================================================================
# Module-level event_bus singleton
# =====================================================================


class TestModuleLevelEventBus:
    """Verify the module-level event_bus singleton works."""

    def test_singleton_exists(self):
        from deerflow.events.bus import event_bus

        assert event_bus is not None

    def test_singleton_is_event_bus(self):
        from deerflow.events.bus import EventBus, event_bus

        assert isinstance(event_bus, EventBus)

    def test_events_init_exports(self):
        from deerflow.events import (
            DomainEvent,
            EventBus,
            EventPublisher,
            EventSubscriber,
            RunCompleted,
            RunCreated,
            event_bus,
            event_registry,
        )

        assert EventBus is not None
        assert EventPublisher is not None
        assert EventSubscriber is not None
        assert DomainEvent is not None
        assert RunCreated is not None
        assert RunCompleted is not None
        assert event_bus is not None
        assert event_registry is not None
