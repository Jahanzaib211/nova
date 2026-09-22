"""`jobs.demo.sleep`: the smoke-test job (`make jobs-demo`).

Sleeps ``seconds`` in one-second steps, reporting progress and honouring
cancellation — everything a real handler must do, with no side effects.
"""

from __future__ import annotations

import asyncio

from deerflow.jobs.context import JobContext
from deerflow.jobs.registry import JobRegistry


async def sleep_job(ctx: JobContext) -> dict:
    total = max(1, int(ctx.payload.get("seconds", 3)))
    for i in range(total):
        await ctx.heartbeat()
        await asyncio.sleep(1)
        await ctx.progress(int((i + 1) * 100 / total), f"slept {i + 1}/{total}s")
    return {"slept": total}


def register(registry: JobRegistry) -> None:
    registry.register("jobs.demo.sleep", sleep_job)
