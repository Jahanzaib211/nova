"""Checkpoint retention: bound LangGraph's checkpoint tables.

LangGraph writes a checkpoint per graph step and never deletes one. On
2026-11-02 Postgres ``checkpoint_blobs`` was 7.8 GB of an ~8 GB database
(4.2 GB of it live ``messages`` blobs -- one full copy of a thread's message
list per step). See :mod:`nova.persistence.checkpoint_retention`.

Every default is the conservative one: the job is **off**, and when switched
on it only **reports** until ``dry_run`` is also turned off.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CheckpointRetentionConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Master switch. When false the jobs-worker handler does nothing and the CLI refuses --apply. Dry-run estimates are always available from the CLI.",
    )
    dry_run: bool = Field(
        default=True,
        description="Report what would be deleted without deleting anything. Must be set false (with enabled: true) for the scheduled job to delete.",
    )
    keep_per_thread: int = Field(
        default=20,
        ge=1,
        description="Newest checkpoints kept for every (thread, namespace). The newest one is what a thread resumes from; the rest are recent history.",
    )
    keep_active_days: float = Field(
        default=7.0,
        ge=0.0,
        description="Threads whose newest checkpoint is younger than this many days are left entirely untouched. 0 applies keep_per_thread to every thread.",
    )
    batch_size: int = Field(
        default=500,
        ge=1,
        le=10_000,
        description="Checkpoints deleted per committed transaction. Bounds lock time and WAL growth per step.",
    )
    max_batches: int = Field(
        default=50,
        ge=1,
        description="Upper bound on batches per sweep, so one sweep cannot run unbounded. The next sweep continues where this one stopped.",
    )
