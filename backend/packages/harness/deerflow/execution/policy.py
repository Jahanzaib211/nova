"""Policy engine for the Nova Execution Kernel.

Phase C7 — declarative admission control.  Every request is evaluated
before any process is created.  Policies are per execution class and
enforce: program allow-lists, sudo gating, timeout clamps, and structural
rules (non-empty argv, no ``shell=True`` — banned by construction since
requests carry argv only).

Usage::

    from deerflow.execution.policy import PolicyEngine

    engine = PolicyEngine()
    decision = engine.evaluate(request)
    if not decision.allowed:
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from deerflow.execution.models import (
    ExecutionClass,
    ExecutionRequest,
    PolicyDecision,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-class policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassPolicy:
    """Constraints applied to one execution class."""

    max_timeout: float = 900.0
    allowed_programs: tuple[str, ...] = ()  # empty → any program
    allow_sudo: bool = False
    # Programs allowed after a leading ``sudo [-n]`` prefix.
    sudo_programs: tuple[str, ...] = ()


_DEFAULT_POLICIES: dict[ExecutionClass, ClassPolicy] = {
    # Shell is intentionally open (sandbox command execution is arbitrary by
    # design) but still timeout-clamped and sudo-gated.
    ExecutionClass.SHELL: ClassPolicy(max_timeout=900.0),
    ExecutionClass.DOCKER: ClassPolicy(
        max_timeout=300.0,
        allowed_programs=("docker", "container"),
    ),
    ExecutionClass.GIT: ClassPolicy(
        max_timeout=120.0,
        allowed_programs=("git",),
    ),
    ExecutionClass.BROWSER: ClassPolicy(max_timeout=120.0),
    ExecutionClass.PYTHON: ClassPolicy(max_timeout=900.0),
    ExecutionClass.PM2: ClassPolicy(
        max_timeout=60.0,
        allowed_programs=("pm2",),
    ),
    ExecutionClass.SYSTEMD: ClassPolicy(
        max_timeout=60.0,
        allowed_programs=("systemctl",),
        allow_sudo=True,
        sudo_programs=("systemctl",),
    ),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class PolicyEngine:
    """Evaluates requests against per-class policies.

    Deterministic and side-effect free: the same request always yields the
    same decision.  Custom policies can be injected for testing or
    deployment-specific hardening.
    """

    policies: dict[ExecutionClass, ClassPolicy] = field(default_factory=lambda: dict(_DEFAULT_POLICIES))

    def evaluate(self, request: ExecutionRequest) -> PolicyDecision:
        if not request.argv:
            return PolicyDecision(allowed=False, reason="empty argv")

        policy = self.policies.get(request.execution_class)
        if policy is None:
            return PolicyDecision(
                allowed=False,
                reason=f"no policy for class {request.execution_class.value}",
            )

        argv = list(request.argv)
        program = argv[0]

        # Sudo gating — strip the sudo prefix and validate the real program.
        if program == "sudo":
            if not policy.allow_sudo:
                return PolicyDecision(
                    allowed=False,
                    reason=f"sudo not permitted for class {request.execution_class.value}",
                )
            rest = [a for a in argv[1:] if not a.startswith("-")]
            if not rest or rest[0] not in policy.sudo_programs:
                return PolicyDecision(
                    allowed=False,
                    reason="sudo target not in sudo_programs allow-list",
                )
        elif policy.allowed_programs and program not in policy.allowed_programs:
            return PolicyDecision(
                allowed=False,
                reason=(f"program {program!r} not allowed for class {request.execution_class.value}"),
            )

        effective_timeout = min(request.limits.timeout, policy.max_timeout)
        return PolicyDecision(allowed=True, effective_timeout=effective_timeout)
