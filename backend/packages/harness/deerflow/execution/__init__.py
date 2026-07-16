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

from deerflow.execution.audit import AuditEngine
from deerflow.execution.kernel import (
    ExecutionDeniedError,
    ExecutionKernel,
    request_with_context,
)
from deerflow.execution.metrics import ExecutionMetrics
from deerflow.execution.models import (
    ExecutionClass,
    ExecutionRecord,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PolicyDecision,
    ResourceLimits,
)
from deerflow.execution.policy import ClassPolicy, PolicyEngine
from deerflow.execution.protocols import (
    ExecutionAdapterProtocol,
    ExecutionKernelProtocol,
)
from deerflow.execution.replay import ReplayEngine
from deerflow.execution.resources import ResourceManager
from deerflow.execution.scheduler import Admission, Scheduler
from deerflow.execution.supervisor import SpawnedProcess, Supervisor

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
