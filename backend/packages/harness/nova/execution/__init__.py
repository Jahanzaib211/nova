"""Nova Execution Kernel — the platform's single execution architecture.

Phase C7 — nothing in Nova executes directly anymore.  Every subprocess,
docker CLI call, git command, pm2/systemctl invocation, and long-running
dev-server spawn flows through :class:`ExecutionKernel` and its adapters.

Layout:

- ``models``       — typed requests/results/records
- ``protocols``    — structural interfaces (kernel + adapters)
- ``policy``       — declarative admission policies per execution class
- ``resources``    — bounded per-class concurrency pools
- ``scheduler``    — policy + resource admission gate
- ``supervisor``   — process registry, TERM→KILL escalation, orphan reaping
- ``audit``        — hash-chained execution audit trail
- ``replay``       — deterministic re-execution of audited runs
- ``metrics``      — per-class counters and latency aggregates
- ``kernel``       — the one sanctioned process-creation site
- ``adapters``     — Shell, Docker, Git, Browser, Python, PM2, Systemd
"""

from nova.execution.audit import AuditEngine
from nova.execution.kernel import (
    ExecutionDeniedError,
    ExecutionKernel,
    request_with_context,
)
from nova.execution.metrics import ExecutionMetrics
from nova.execution.models import (
    ExecutionClass,
    ExecutionRecord,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PolicyDecision,
    ResourceLimits,
)
from nova.execution.policy import ClassPolicy, PolicyEngine
from nova.execution.protocols import (
    ExecutionAdapterProtocol,
    ExecutionKernelProtocol,
)
from nova.execution.replay import ReplayEngine
from nova.execution.resources import ResourceManager
from nova.execution.scheduler import Admission, Scheduler
from nova.execution.supervisor import SpawnedProcess, Supervisor

__all__ = [
    "Admission",
    "AuditEngine",
    "ClassPolicy",
    "ExecutionAdapterProtocol",
    "ExecutionClass",
    "ExecutionDeniedError",
    "ExecutionKernel",
    "ExecutionKernelProtocol",
    "ExecutionMetrics",
    "ExecutionRecord",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "PolicyDecision",
    "PolicyEngine",
    "ReplayEngine",
    "ResourceLimits",
    "ResourceManager",
    "Scheduler",
    "SpawnedProcess",
    "Supervisor",
    "request_with_context",
]
