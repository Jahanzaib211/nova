# Execution Kernel (Phase C7 → C8)

Nova has exactly **one** execution architecture. Nothing in the platform
creates OS processes directly — every subprocess, docker CLI call, git
command, pm2/systemctl invocation, and long-running dev-server spawn flows
through the Execution Kernel at
`packages/harness/deerflow/execution/`.

## Pipeline

Every execution follows the same deterministic pipeline:

```
ExecutionRequest
  → Scheduler        (policy evaluation + resource-slot acquisition)
  → Kernel           (the one sanctioned subprocess.Popen site)
  → Supervisor       (registration, timeout, TERM→grace→KILL, cancellation)
  → ExecutionResult  (typed; process failures never raise)
  → AuditEngine      (hash-chained record)  +  Metrics  +  Domain events
```

## Components

| Module | Responsibility |
|---|---|
| `models.py` | `ExecutionRequest`, `ExecutionResult`, `ExecutionStatus`, `ExecutionClass`, `ResourceLimits`, `ExecutionRecord` — all frozen dataclasses. Phase C8 adds `parent_execution_id`, `session_id`, and execution budget fields to `ResourceLimits` |
| `pty_manager.py` | Phase C8 PTY allocation (`openpty`), window-size control (`TIOCSWINSZ`), file-descriptor management for interactive shell sessions |
| `session_registry.py` | Phase C8 `ShellSession` lifecycle + `SessionRegistry` tracking: session state, PTY fds, pid, cwd, env, terminal size, heartbeat, I/O metrics |
| `protocols.py` | `ExecutionKernelProtocol`, `ExecutionAdapterProtocol` (structural typing, Phase C1 style) |
| `policy.py` | Per-class admission: program allow-lists, sudo gating (systemctl only), timeout clamps. `shell=True` is impossible by construction — requests carry argv only |
| `resources.py` | Bounded per-class concurrency pools; saturation → typed `DENIED`, never unbounded fan-out |
| `scheduler.py` | Phase C8 admission gate with depth/child tracking (`DepthTracker`, `ChildTracker`) and execution budget enforcement (`max_depth`, `max_children`, `max_recursion`) |
| `supervisor.py` | Process registry, process-**group** TERM→KILL escalation, cancellation by id, orphan reconciliation at shutdown. Phase C8 adds: bidirectional ownership maps (execution_id ↔ run_id ↔ session_id ↔ pid), `ProcessHeartbeat` tracking, zombie detection, recursive child cancellation |
| `audit.py` | Ring buffer of `ExecutionRecord`s, sha256 hash-chained (`verify_chain()`); env **values** never stored, only key names |
| `replay.py` | Rebuild + re-execute any audited execution (`dry_run` / `replay`) |
| `metrics.py` | Per-class counters and latency aggregates |
| `kernel.py` | `execute_sync` (thread-safe primitive), `execute` (async facade via `to_thread`), `spawn` (supervised long-running processes with full ownership metadata), `cancel` (Phase C8 two-phase: SIGINT → SIGTERM → SIGKILL), `_two_phase_cancel`, heartbeat thread, `shutdown`, `snapshot` |
| `adapters/` | Shell, Docker, Git, Browser, Python, PM2, Systemd, **InteractiveShell** (Phase C8: full PTY with TIOCSWINSZ resize, heartbeat thread, SIGWINCH propagation) |
| `testing.py` | `FakeExecutionKernel` for unit tests; `FakeSupervisor` for Phase C8 supervisor/ownership tests |

## Execution classes and default policy

| Class | Programs | Max timeout | Slots | Sudo |
|---|---|---|---|---|
| `shell` | any | 900 s | 8 | no |
| `docker` | `docker`, `container` | 300 s | 4 | no |
| `git` | `git` | 120 s | 8 | no |
| `browser` | (CDP sessions, no processes) | 120 s | 4 | no |
| `python` | any (`sys.executable`) | 900 s | 4 | no |
| `pm2` | `pm2` | 60 s | 2 | no |
| `systemd` | `systemctl` | 60 s | 2 | `sudo -n systemctl` only |

## Usage

Resolve the kernel through DI — never construct one ad hoc:

```python
from deerflow.services.container import service_container
from deerflow.execution.adapters import GitAdapter

kernel = service_container.execution_kernel()
result = GitAdapter(kernel).run("/repo", "status", "--porcelain")
if result.ok:
    print(result.stdout)
```

In gateway routers, use the typed dependency:

```python
from app.gateway.deps import get_execution_kernel
```

Long-running processes (dev servers):

```python
handle = await kernel.spawn(ExecutionRequest(argv=("/bin/sh", "-c", cmd), ...))
line = await handle.stdout.readline()
await handle.terminate_gracefully(5)   # TERM → grace → KILL, whole group
```

## Events

The kernel emits `ExecutionRequested`, `ExecutionStarted`,
`ExecutionCompleted` / `ExecutionFailed` / `ExecutionTimedOut` /
`ExecutionCancelled` / `ExecutionDenied`, and `ProcessSpawned` /
`ProcessExited` on the Phase C3 event bus, each carrying correlation_id,
run_id, thread_id, and an execution payload.

## Testing

```python
from deerflow.execution.testing import FakeExecutionKernel
from deerflow.services.container import service_container

fake = FakeExecutionKernel(lambda req: (0, "stdout", ""))
service_container.override(execution_kernel=fake)
# ... exercise code ...
assert fake.requests[0].argv[0] == "docker"
service_container.reset()
```

## Guardrails

`tests/test_execution_guardrails.py` fails CI if any direct execution
primitive (`subprocess.run/Popen/...`, `create_subprocess_*`,
`os.system`, `os.popen`, `shell=True`) appears in `backend/packages` or
`backend/app` outside `deerflow/execution/`.

## Shutdown semantics

The gateway lifespan calls `kernel.shutdown()` on exit, which reconciles
every tracked process (process-group TERM→KILL). Kernel-created processes
are session leaders (`start_new_session=True`), so `sh -c` grandchildren
die with their parents — no orphan keeps a pipe or port open.

## Out of scope (documented debt)

- `scripts/healthcheck-daemon.py` runs **out-of-process** under pm2 as the
  watchdog of last resort (it must survive gateway death), so it keeps its
  own subprocess calls. The in-gateway recovery paths that used to import
  from it now go through the kernel's PM2/Systemd adapters.
- `scripts/{check,doctor,sync_labels,...}.py` are developer/CI tools that
  run outside the Nova runtime.
- `aio_sandbox_provider.py` registers SIGTERM/SIGINT/SIGHUP *cleanup
  handlers* (not execution); unifying signal ownership under the
  supervisor is a Phase C8 candidate.
