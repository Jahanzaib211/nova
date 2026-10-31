"""Job runner configuration (``jobs:`` in config.yaml).

The worker process (``python -m app.jobs.worker``) reads this once at start;
the gateway only enqueues and reads. Startup-only: see reload_boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JobsConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable the job runner (the gateway's /api/jobs surface and the worker).")
    queues: list[str] = Field(default_factory=lambda: ["default"], description="Queues this worker claims from, in priority order.")
    concurrency: int = Field(default=4, ge=1, le=64, description="Jobs one worker runs at the same time.")
    lease_ttl_seconds: int = Field(default=60, ge=5, description="How long a claimed job stays leased without a heartbeat before the reaper retries it.")
    poll_interval_seconds: float = Field(default=1.0, ge=0.1, description="Idle sleep between claim attempts.")
    reaper_interval_seconds: float = Field(default=15.0, ge=1.0, description="How often expired leases are returned to the queue.")
    scheduler_interval_seconds: float = Field(default=30.0, ge=1.0, description="How often cron schedules are checked.")
    shutdown_grace_seconds: float = Field(default=30.0, ge=0.0, description="On SIGTERM, how long in-flight jobs may finish before their leases are released.")
    events_retention_days: int = Field(default=14, ge=1, description="Prune job_events of terminal jobs older than this.")
    worker_id: str | None = Field(default=None, description="Override the worker id (default: hostname-pid).")
