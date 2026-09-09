"""Run event storage configuration.

Controls where run events (messages + execution traces) are persisted.

Backends:
- memory: In-memory storage, data lost on restart. Suitable for
  development and testing.
- db: SQL database via SQLAlchemy ORM. Provides full query capability.
  Suitable for production deployments.
- jsonl: Append-only JSONL files. Lightweight alternative for
  single-node deployments that need persistence without a database.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RunEventsConfig(BaseModel):
    backend: Literal["memory", "db", "jsonl"] = Field(
        default="memory",
        description="Storage backend for run events. 'memory' for development (no persistence), 'db' for production (SQL queries), 'jsonl' for lightweight single-node persistence.",
    )
    max_trace_content: int = Field(
        default=10240,
        description="Maximum trace content size in bytes before truncation (db backend only).",
    )
    track_token_usage: bool = Field(
        default=True,
        description="Whether RunJournal should accumulate token counts to RunRow.",
    )
    retention_days: int = Field(
        default=0,
        ge=0,
        description=(
            "Delete run events older than this many days. 0 disables pruning and keeps "
            "everything, which is the historical behaviour and stays the default -- "
            "silently deleting a user's conversation history on upgrade would be worse "
            "than the growth. Set it explicitly: the table is otherwise unbounded, and on "
            "this deployment it reached 366 MB (335 MB of genuine content) with no bound "
            "at all, second only to the checkpoint tables."
        ),
    )
