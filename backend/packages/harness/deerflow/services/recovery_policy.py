"""Declarative recovery policies for Nova.

Phase C4 — each policy defines how to recover from a specific failure mode.
Policies are data, not logic: trigger event, retry strategy, backoff,
timeout, max retries, escalation condition, success condition.

Usage::

    from deerflow.services.recovery_policy import (
        RecoveryPolicy,
        TunnelDisconnectedPolicy,
        select_policy,
    )

    policy = select_policy("tunnel_disconnected")
    assert policy.max_retries == 3
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetryStrategy:
    """Declarative retry configuration."""

    max_retries: int = 3
    base_delay_ms: float = 1000.0
    max_delay_ms: float = 30_000.0
    backoff_factor: float = 2.0
    jitter_fraction: float = 0.3

    def delay_for_attempt(self, attempt: int) -> float:
        """Compute delay in ms for the given attempt (1-indexed)."""
        import random

        delay = self.base_delay_ms * (self.backoff_factor ** max(0, attempt - 1))
        delay = min(delay, self.max_delay_ms)
        jitter = delay * self.jitter_fraction * (2 * random.random() - 1)
        return max(0, delay + jitter)


@dataclass(frozen=True)
class RecoveryPolicy:
    """Declarative recovery policy for a specific failure mode.

    Each policy is a pure data object.  The RecoveryService reads the
    policy to decide what to do — the policy itself contains no logic.
    """

    name: str
    trigger_event: str
    component: str
    action: str
    retry: RetryStrategy = field(default_factory=RetryStrategy)
    timeout_ms: float = 30_000.0
    escalation_event: str = ""
    success_event: str = ""
    description: str = ""

    @property
    def max_retries(self) -> int:
        return self.retry.max_retries

    @property
    def base_delay_ms(self) -> float:
        return self.retry.base_delay_ms


# ---------------------------------------------------------------------------
# Concrete policies
# ---------------------------------------------------------------------------

TUNNEL_DISCONNECTED = RecoveryPolicy(
    name="tunnel_disconnected",
    trigger_event="HealthChanged",
    component="tunnel",
    action="restart_tunnel",
    retry=RetryStrategy(max_retries=3, base_delay_ms=2_000, backoff_factor=2.0),
    timeout_ms=30_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Cloudflare tunnel disconnected — reset-failed + restart + verify",
)

GATEWAY_UNAVAILABLE = RecoveryPolicy(
    name="gateway_unavailable",
    trigger_event="HealthChanged",
    component="gateway",
    action="restart_gateway",
    retry=RetryStrategy(max_retries=2, base_delay_ms=5_000, backoff_factor=3.0),
    timeout_ms=60_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Gateway process unresponsive — PM2 restart",
)

STREAM_STALLED = RecoveryPolicy(
    name="stream_stalled",
    trigger_event="HealthChanged",
    component="stream",
    action="reconnect_stream",
    retry=RetryStrategy(max_retries=2, base_delay_ms=1_000, backoff_factor=2.0),
    timeout_ms=15_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="SSE stream stalled — force disconnect + client reconnect",
)

BROWSER_DISCONNECTED = RecoveryPolicy(
    name="browser_disconnected",
    trigger_event="HealthChanged",
    component="browser",
    action="restart_browser",
    retry=RetryStrategy(max_retries=2, base_delay_ms=500, backoff_factor=2.0),
    timeout_ms=10_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Browser preview disconnected — restart preview session",
)

BROWSER_CRASH = RecoveryPolicy(
    name="browser_crash",
    trigger_event="HealthChanged",
    component="browser",
    action="restart_browser",
    retry=RetryStrategy(max_retries=3, base_delay_ms=1_000, backoff_factor=2.0),
    timeout_ms=15_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Browser crashed — restart with circuit breaker",
)

SANDBOX_UNAVAILABLE = RecoveryPolicy(
    name="sandbox_unavailable",
    trigger_event="HealthChanged",
    component="sandbox",
    action="restart_sandbox",
    retry=RetryStrategy(max_retries=2, base_delay_ms=2_000, backoff_factor=3.0),
    timeout_ms=30_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Sandbox container unavailable — recreate",
)

HEALTH_DEGRADED = RecoveryPolicy(
    name="health_degraded",
    trigger_event="HealthChanged",
    component="health",
    action="investigate",
    retry=RetryStrategy(max_retries=1, base_delay_ms=5_000),
    timeout_ms=30_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Health check degraded — probe and diagnose",
)

CONTAINER_RESTART = RecoveryPolicy(
    name="container_restart",
    trigger_event="HealthChanged",
    component="container",
    action="restart_container",
    retry=RetryStrategy(max_retries=2, base_delay_ms=3_000, backoff_factor=2.0),
    timeout_ms=60_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Docker container unhealthy — restart via PM2/systemd",
)

ORPHAN_RUN = RecoveryPolicy(
    name="orphan_run",
    trigger_event="RunFailed",
    component="run",
    action="reap_orphan",
    retry=RetryStrategy(max_retries=1, base_delay_ms=0),
    timeout_ms=10_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Orphaned run detected — mark as failed and clean up",
)

WORKER_EXITED = RecoveryPolicy(
    name="worker_exited",
    trigger_event="RunFailed",
    component="worker",
    action="restart_worker",
    retry=RetryStrategy(max_retries=3, base_delay_ms=1_000, backoff_factor=2.0),
    timeout_ms=30_000,
    escalation_event="RecoveryEscalated",
    success_event="RecoverySucceeded",
    description="Worker process exited — restart and reattach",
)

# ---------------------------------------------------------------------------
# Policy registry
# ---------------------------------------------------------------------------

POLICIES: dict[str, RecoveryPolicy] = {
    p.name: p for p in [
        TUNNEL_DISCONNECTED,
        GATEWAY_UNAVAILABLE,
        STREAM_STALLED,
        BROWSER_DISCONNECTED,
        BROWSER_CRASH,
        SANDBOX_UNAVAILABLE,
        HEALTH_DEGRADED,
        CONTAINER_RESTART,
        ORPHAN_RUN,
        WORKER_EXITED,
    ]
}


def select_policy(name: str) -> RecoveryPolicy | None:
    """Look up a recovery policy by name."""
    return POLICIES.get(name)


def policies_for_trigger(event_type: str) -> list[RecoveryPolicy]:
    """Return all policies that trigger on the given event type."""
    return [p for p in POLICIES.values() if p.trigger_event == event_type]


def all_policies() -> list[RecoveryPolicy]:
    """Return all registered policies."""
    return list(POLICIES.values())
