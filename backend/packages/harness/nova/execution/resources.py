"""Resource manager for the Nova Execution Kernel.

Phase C7 — bounded per-class concurrency.  Each execution class has a
semaphore-backed slot pool; acquisition is blocking with a queue timeout
so saturation degrades into a typed DENIED result instead of unbounded
process fan-out.

Thread-safe: the kernel's synchronous engine runs on arbitrary threads
(sync call sites, ``asyncio.to_thread``), so plain ``threading`` primitives
are used rather than asyncio ones.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from deerflow.execution.models import ExecutionClass

logger = logging.getLogger(__name__)

_DEFAULT_SLOTS: dict[ExecutionClass, int] = {
    ExecutionClass.SHELL: 8,
    ExecutionClass.DOCKER: 4,
    ExecutionClass.GIT: 8,
    ExecutionClass.BROWSER: 4,
    ExecutionClass.PYTHON: 4,
    ExecutionClass.PM2: 2,
    ExecutionClass.SYSTEMD: 2,
}


@dataclass
class ResourceManager:
    """Per-class concurrency pools.

    ``acquire`` blocks up to ``queue_timeout`` seconds for a slot and
    returns False on saturation.  ``release`` must be called exactly once
    per successful acquire (the kernel guarantees this with try/finally).
    """

    slots: dict[ExecutionClass, int] = field(default_factory=lambda: dict(_DEFAULT_SLOTS))

    def __post_init__(self) -> None:
        self._semaphores: dict[ExecutionClass, threading.BoundedSemaphore] = {cls: threading.BoundedSemaphore(count) for cls, count in self.slots.items()}
        self._in_flight: dict[ExecutionClass, int] = dict.fromkeys(self.slots, 0)
        self._lock = threading.Lock()

    def acquire(self, execution_class: ExecutionClass, queue_timeout: float) -> bool:
        sem = self._semaphores.get(execution_class)
        if sem is None:
            # Unknown class — no pool configured; allow but log loudly.
            logger.warning("No resource pool for class %s", execution_class.value)
            return True
        acquired = sem.acquire(timeout=queue_timeout)
        if acquired:
            with self._lock:
                self._in_flight[execution_class] += 1
        return acquired

    def release(self, execution_class: ExecutionClass) -> None:
        sem = self._semaphores.get(execution_class)
        if sem is None:
            return
        with self._lock:
            self._in_flight[execution_class] -= 1
        sem.release()

    def in_flight(self, execution_class: ExecutionClass) -> int:
        with self._lock:
            return self._in_flight.get(execution_class, 0)

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                cls.value: {
                    "limit": self.slots[cls],
                    "in_flight": self._in_flight[cls],
                }
                for cls in self.slots
            }
