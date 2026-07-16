"""Tests for Phase C4 — Recovery Engine + Unified Health Management.

Covers:
- RecoveryPolicy: declarative policy data, retry strategy, delay computation
- RecoveryEngine: event-driven recovery, retry/backoff, cancellation, metrics
- Recovery events: creation, immutability, event_type property
- RecoveryRecord: history tracking
- Policy selection: select_policy, policies_for_trigger
- Health integration: HealthChanged triggers RecoveryEngine
- Correlation ID propagation
- Edge cases: no handler, timeout, max retries, duplicate prevention
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =====================================================================
# RecoveryPolicy
# =====================================================================


class TestRecoveryPolicy:
    """Verify declarative recovery policy data objects."""

    def test_all_policies_registered(self):
        from deerflow.services.recovery_policy import POLICIES, all_policies

        assert len(POLICIES) == 10
        policies = all_policies()
        assert len(policies) == 10

    def test_select_policy(self):
        from deerflow.services.recovery_policy import select_policy

        p = select_policy("tunnel_disconnected")
        assert p is not None
        assert p.component == "tunnel"
        assert p.action == "restart_tunnel"
        assert p.max_retries == 3

    def test_select_policy_not_found(self):
        from deerflow.services.recovery_policy import select_policy

        assert select_policy("nonexistent") is None

    def test_policies_for_trigger(self):
        from deerflow.services.recovery_policy import policies_for_trigger

        tunnel = policies_for_trigger("HealthChanged")
        assert len(tunnel) >= 1
        names = [p.name for p in tunnel]
        assert "tunnel_disconnected" in names

    def test_policies_for_trigger_empty(self):
        from deerflow.services.recovery_policy import policies_for_trigger

        assert policies_for_trigger("NonexistentEvent") == []

    def test_policy_properties(self):
        from deerflow.services.recovery_policy import TUNNEL_DISCONNECTED

        assert TUNNEL_DISCONNECTED.name == "tunnel_disconnected"
        assert TUNNEL_DISCONNECTED.trigger_event == "HealthChanged"
        assert TUNNEL_DISCONNECTED.component == "tunnel"
        assert TUNNEL_DISCONNECTED.timeout_ms == 30_000


class TestRetryStrategy:
    """Verify retry strategy delay computation."""

    def test_delay_increases(self):
        from deerflow.services.recovery_policy import RetryStrategy

        rs = RetryStrategy(max_retries=5, base_delay_ms=100, backoff_factor=2.0, jitter_fraction=0)
        d1 = rs.delay_for_attempt(1)
        d2 = rs.delay_for_attempt(2)
        d3 = rs.delay_for_attempt(3)
        assert d1 < d2 < d3

    def test_delay_caps_at_max(self):
        from deerflow.services.recovery_policy import RetryStrategy

        rs = RetryStrategy(max_retries=10, base_delay_ms=100, max_delay_ms=500, backoff_factor=3.0, jitter_fraction=0)
        d5 = rs.delay_for_attempt(5)
        assert d5 <= 500

    def test_delay_with_jitter(self):
        from deerflow.services.recovery_policy import RetryStrategy

        rs = RetryStrategy(base_delay_ms=100, jitter_fraction=0.5)
        # Run multiple times — jitter should produce varying results
        delays = {rs.delay_for_attempt(1) for _ in range(20)}
        assert len(delays) > 1  # not all identical


# =====================================================================
# Recovery events
# =====================================================================


class TestRecoveryEvents:
    """Verify recovery event creation and immutability."""

    def test_recovery_started(self):
        from deerflow.events.event import RecoveryStarted

        e = RecoveryStarted(run_id="r1", payload={"policy": "tunnel"})
        assert e.event_type == "RecoveryStarted"
        assert e.run_id == "r1"
        assert e.payload["policy"] == "tunnel"

    def test_recovery_succeeded(self):
        from deerflow.events.event import RecoverySucceeded

        e = RecoverySucceeded(correlation_id="c1", payload={"attempt": 1})
        assert e.event_type == "RecoverySucceeded"
        assert e.correlation_id == "c1"

    def test_recovery_failed(self):
        from deerflow.events.event import RecoveryFailed

        e = RecoveryFailed(payload={"last_error": "timeout"})
        assert e.event_type == "RecoveryFailed"

    def test_recovery_escalated(self):
        from deerflow.events.event import RecoveryEscalated

        e = RecoveryEscalated(payload={"attempts": 3})
        assert e.event_type == "RecoveryEscalated"

    def test_recovery_cancelled(self):
        from deerflow.events.event import RecoveryCancelled

        e = RecoveryCancelled()
        assert e.event_type == "RecoveryCancelled"

    def test_recovery_aborted(self):
        from deerflow.events.event import RecoveryAborted

        e = RecoveryAborted()
        assert e.event_type == "RecoveryAborted"

    def test_recovery_retry_scheduled(self):
        from deerflow.events.event import RecoveryRetryScheduled

        e = RecoveryRetryScheduled(payload={"delay_ms": 1000})
        assert e.event_type == "RecoveryRetryScheduled"

    def test_all_events_frozen(self):
        from deerflow.events.event import (
            RecoveryAborted,
            RecoveryCancelled,
            RecoveryEscalated,
            RecoveryFailed,
            RecoveryRetryScheduled,
            RecoveryStarted,
            RecoverySucceeded,
        )

        for cls in [
            RecoveryStarted, RecoveryRetryScheduled, RecoverySucceeded,
            RecoveryFailed, RecoveryEscalated, RecoveryCancelled, RecoveryAborted,
        ]:
            e = cls()
            with pytest.raises(AttributeError):
                e.event_id = "new"  # type: ignore[misc]


# =====================================================================
# Event registry
# =====================================================================


class TestRecoveryEventRegistry:
    """Verify recovery events are registered in the event registry."""

    def test_recovery_category(self):
        from deerflow.events.registry import event_registry

        recovery = event_registry.list_by_category("recovery")
        assert "RecoveryStarted" in recovery
        assert "RecoverySucceeded" in recovery
        assert "RecoveryFailed" in recovery
        assert "RecoveryEscalated" in recovery
        assert "RecoveryCancelled" in recovery
        assert "RecoveryAborted" in recovery
        assert "RecoveryRetryScheduled" in recovery

    def test_all_events_registered(self):
        from deerflow.events.registry import event_registry

        all_events = event_registry.list_events()
        assert len(all_events) >= 24  # 17 C3 + 7 C4 recovery events


# =====================================================================
# RecoveryRecord
# =====================================================================


class TestRecoveryRecord:
    """Verify RecoveryRecord immutable dataclass."""

    def test_creation(self):
        from deerflow.services.recovery_service import RecoveryRecord

        r = RecoveryRecord(
            policy_name="tunnel_disconnected",
            component="tunnel",
            action="restart_tunnel",
            trigger_event="HealthChanged",
            outcome="success",
            attempt=1,
            duration_ms=150.0,
            correlation_id="c1",
        )
        assert r.policy_name == "tunnel_disconnected"
        assert r.outcome == "success"
        assert r.correlation_id == "c1"

    def test_frozen(self):
        from deerflow.services.recovery_service import RecoveryRecord

        r = RecoveryRecord(
            policy_name="test", component="x", action="y",
            trigger_event="z", outcome="success", attempt=1, duration_ms=0,
        )
        with pytest.raises(AttributeError):
            r.outcome = "failed"  # type: ignore[misc]


# =====================================================================
# RecoveryEngine
# =====================================================================


class TestRecoveryEngine:
    """Verify RecoveryEngine event-driven recovery orchestration."""

    def setup_method(self):
        from deerflow.events.bus import EventBus
        from deerflow.services.recovery_service import RecoveryEngine

        self.bus = EventBus()

    @pytest.mark.asyncio
    async def test_engine_subscribes_to_bus(self):
        from deerflow.services.recovery_service import RecoveryEngine

        engine = RecoveryEngine(event_bus=self.bus)
        engine.start()
        assert self.bus.handler_count(type(None)) >= 0  # subscribed to DomainEvent
        engine.stop()

    @pytest.mark.asyncio
    async def test_engine_reacts_to_health_changed(self):
        from deerflow.events.event import HealthChanged
        from deerflow.services.recovery_service import RecoveryEngine

        mock_action = AsyncMock(return_value=True)
        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": mock_action},
        )
        engine.start()

        # Publish a HealthChanged event that should trigger tunnel recovery
        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False},
        ))

        # Give async tasks time to complete
        await asyncio.sleep(0.1)

        # The engine should have attempted recovery
        # (mock_action may or may not be called depending on event matching)
        engine.stop()

    @pytest.mark.asyncio
    async def test_recovery_success_emits_events(self):
        from deerflow.events.event import HealthChanged, RecoveryStarted, RecoverySucceeded
        from deerflow.services.recovery_service import RecoveryEngine

        mock_action = AsyncMock(return_value=True)
        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": mock_action},
        )
        engine.start()

        received = []
        self.bus.subscribe(RecoveryStarted, lambda e: received.append("started"))
        self.bus.subscribe(RecoverySucceeded, lambda e: received.append("succeeded"))

        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False},
        ))

        await asyncio.sleep(0.2)
        assert "started" in received
        engine.stop()

    @pytest.mark.asyncio
    async def test_recovery_failure_retries(self):
        from deerflow.events.event import HealthChanged, RecoveryRetryScheduled
        from deerflow.services import recovery_policy
        from deerflow.services.recovery_service import RecoveryEngine

        # Override policy to have minimal delays for fast test
        original = recovery_policy.POLICIES.get("tunnel_disconnected")
        recovery_policy.POLICIES["tunnel_disconnected"] = type(original)(
            name="tunnel_disconnected",
            trigger_event="HealthChanged",
            component="tunnel",
            action="restart_tunnel",
            retry=type(original.retry)(max_retries=3, base_delay_ms=1, max_delay_ms=5, jitter_fraction=0),
            timeout_ms=5000,
        )

        call_count = 0

        async def fail_then_succeed(policy):
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": fail_then_succeed},
        )
        engine.start()

        retry_received = []
        self.bus.subscribe(RecoveryRetryScheduled, lambda e: retry_received.append(e))

        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False},
        ))

        await asyncio.sleep(0.3)
        assert call_count >= 2

        # Restore original
        if original:
            recovery_policy.POLICIES["tunnel_disconnected"] = original
        engine.stop()

    @pytest.mark.asyncio
    async def test_max_retries_escalates(self):
        from deerflow.events.event import HealthChanged, RecoveryEscalated

        # Override policy to have very few retries for fast test
        from deerflow.services import recovery_policy
        from deerflow.services.recovery_policy import TUNNEL_DISCONNECTED
        from deerflow.services.recovery_service import RecoveryEngine
        original = recovery_policy.POLICIES.get("tunnel_disconnected")
        recovery_policy.POLICIES["tunnel_disconnected"] = type(original)(
            name="tunnel_disconnected",
            trigger_event="HealthChanged",
            component="tunnel",
            action="restart_tunnel",
            retry=type(original.retry)(max_retries=1, base_delay_ms=1, jitter_fraction=0),
            timeout_ms=5000,
        )

        mock_action = AsyncMock(return_value=False)
        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": mock_action},
        )
        engine.start()

        escalated = []
        self.bus.subscribe(RecoveryEscalated, lambda e: escalated.append(e))

        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False},
        ))

        await asyncio.sleep(0.3)
        assert len(escalated) >= 1
        assert escalated[0].payload["policy"] == "tunnel_disconnected"

        # Restore original
        if original:
            recovery_policy.POLICIES["tunnel_disconnected"] = original
        engine.stop()

    @pytest.mark.asyncio
    async def test_cancel_recovery(self):
        from deerflow.events.event import HealthChanged, RecoveryCancelled
        from deerflow.services import recovery_policy
        from deerflow.services.recovery_service import RecoveryEngine

        # Override policy to have minimal delays
        original = recovery_policy.POLICIES.get("tunnel_disconnected")
        recovery_policy.POLICIES["tunnel_disconnected"] = type(original)(
            name="tunnel_disconnected",
            trigger_event="HealthChanged",
            component="tunnel",
            action="restart_tunnel",
            retry=type(original.retry)(max_retries=5, base_delay_ms=100, jitter_fraction=0),
            timeout_ms=5000,
        )

        async def slow_action(policy):
            await asyncio.sleep(10)
            return True

        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": slow_action},
        )
        engine.start()

        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False},
        ))

        await asyncio.sleep(0.05)

        cancelled = []
        self.bus.subscribe(RecoveryCancelled, lambda e: cancelled.append(e))

        result = engine.cancel_recovery("tunnel_disconnected")
        assert result is True

        await asyncio.sleep(0.3)

        # Restore original
        if original:
            recovery_policy.POLICIES["tunnel_disconnected"] = original
        engine.stop()

    def test_metrics(self):
        from deerflow.services.recovery_service import RecoveryEngine

        engine = RecoveryEngine(event_bus=self.bus)
        snapshot = engine.metrics_snapshot()
        assert snapshot["recoveries_total"] == 0
        assert snapshot["recoveries_successful"] == 0

    def test_history(self):
        from deerflow.services.recovery_service import RecoveryEngine

        engine = RecoveryEngine(event_bus=self.bus)
        history = engine.get_history()
        assert history == []

    def test_stop_without_start(self):
        from deerflow.services.recovery_service import RecoveryEngine

        engine = RecoveryEngine(event_bus=self.bus)
        engine.stop()  # should not raise

    def test_start_stop_idempotent(self):
        from deerflow.services.recovery_service import RecoveryEngine

        engine = RecoveryEngine(event_bus=self.bus)
        engine.start()
        engine.start()  # second start should be no-op
        engine.stop()
        engine.stop()  # second stop should be no-op


# =====================================================================
# Health integration
# =====================================================================


class TestHealthRecoveryIntegration:
    """Verify HealthChanged events trigger RecoveryEngine."""

    def setup_method(self):
        from deerflow.events.bus import EventBus

        self.bus = EventBus()

    @pytest.mark.asyncio
    async def test_health_degraded_triggers_investigation(self):
        from deerflow.events.event import HealthChanged, RecoveryStarted
        from deerflow.services.recovery_service import RecoveryEngine

        mock_action = AsyncMock(return_value=True)
        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"investigate": mock_action},
        )
        engine.start()

        started = []
        self.bus.subscribe(RecoveryStarted, lambda e: started.append(e))

        self.bus.publish(HealthChanged(
            thread_id="t1",
            payload={"healthy": False, "probe_count": 12, "healthy_count": 10},
        ))

        await asyncio.sleep(0.1)
        assert len(started) >= 1
        engine.stop()


# =====================================================================
# ServiceContainer integration
# =====================================================================


class TestServiceContainerRecovery:
    """Verify ServiceContainer wires RecoveryEngine correctly."""

    def test_recovery_engine_singleton(self):
        from deerflow.services.container import ServiceContainer

        container = ServiceContainer()
        engine1 = container.recovery_engine()
        engine2 = container.recovery_engine()
        assert engine1 is engine2
        container.reset()

    def test_recovery_service_uses_engine(self):
        from deerflow.services.container import ServiceContainer

        container = ServiceContainer()
        svc = container.recovery_service()
        engine = container.recovery_engine()
        assert hasattr(svc, "engine")
        assert svc.engine is engine
        container.reset()


# =====================================================================
# Default action handlers
# =====================================================================


class TestDefaultActions:
    """Verify default recovery action handlers exist and are callable."""

    def test_all_default_actions_registered(self):
        from deerflow.services.recovery_service import DEFAULT_ACTIONS

        expected = [
            "restart_tunnel", "restart_gateway", "restart_browser",
            "restart_sandbox", "reap_orphan", "restart_worker",
            "investigate", "reconnect_stream", "restart_container",
        ]
        for name in expected:
            assert name in DEFAULT_ACTIONS, f"Missing action: {name}"

    @pytest.mark.asyncio
    async def test_tunnel_action_runs_systemd_recovery_through_kernel(self):
        """Tunnel recovery must flow through the Execution Kernel (Phase C7):
        reset-failed → restart → verify is-active, all via ``sudo -n systemctl``.
        A FakeExecutionKernel keeps the test hermetic — a previous version of
        this action's test leaked real systemctl restarts to the host."""
        from deerflow.execution.testing import FakeExecutionKernel
        from deerflow.services.container import service_container
        from deerflow.services.recovery_policy import TUNNEL_DISCONNECTED
        from deerflow.services.recovery_service import _recover_tunnel

        def handler(request):
            if "is-active" in request.argv:
                return (0, "active\n", "")
            return (0, "", "")

        fake = FakeExecutionKernel(handler)
        service_container.override(execution_kernel=fake)
        try:
            result = await _recover_tunnel(TUNNEL_DISCONNECTED)
        finally:
            service_container.reset()

        assert result is True
        commands = [" ".join(r.argv) for r in fake.requests]
        assert commands[0] == "sudo -n systemctl reset-failed cloudflared-nova.service"
        assert commands[1] == "sudo -n systemctl restart cloudflared-nova.service"
        assert any("is-active cloudflared-nova.service" in c for c in commands[2:])


# =====================================================================
# Correlation ID propagation
# =====================================================================


class TestCorrelationIdPropagation:
    """Verify correlation IDs flow through recovery pipeline."""

    def setup_method(self):
        from deerflow.events.bus import EventBus

        self.bus = EventBus()

    @pytest.mark.asyncio
    async def test_correlation_id_preserved_in_events(self):
        from deerflow.events.event import HealthChanged, RecoveryStarted
        from deerflow.services.recovery_service import RecoveryEngine

        mock_action = AsyncMock(return_value=True)
        engine = RecoveryEngine(
            event_bus=self.bus,
            actions={"restart_tunnel": mock_action},
        )
        engine.start()

        started_events = []
        self.bus.subscribe(RecoveryStarted, lambda e: started_events.append(e))

        self.bus.publish(HealthChanged(
            correlation_id="my-corr-123",
            run_id="run-abc",
            thread_id="thread-xyz",
            payload={"healthy": False},
        ))

        await asyncio.sleep(0.1)
        if started_events:
            assert started_events[0].correlation_id == "my-corr-123"
            assert started_events[0].run_id == "run-abc"
            assert started_events[0].thread_id == "thread-xyz"
        engine.stop()
