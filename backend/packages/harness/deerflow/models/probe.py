"""The "Check model" probe: one tiny completion against a configured model.

``POST /api/models/{name}/test`` and the ``models.probe`` capability both
answer "does this model respond?". They used to implement it separately and
drifted: the capability had no timeout (a stopped local server held the
call open indefinitely) and attached tracing callbacks, so every health
probe landed in Langfuse/LangSmith as a trace. Both call :func:`probe_model`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

#: The prompt every "is it alive?" round trip sends — models and ACP runtimes alike.
PROBE_PROMPT = "Reply with the single word: ok"
PROBE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class ModelProbeResult:
    ok: bool
    latency_ms: int
    #: The reply's text on success, the exception on failure.
    detail: str
    timed_out: bool = False


async def probe_model(name: str, *, app_config: Any | None = None, timeout: float = PROBE_TIMEOUT_SECONDS) -> ModelProbeResult:
    """Send :data:`PROBE_PROMPT` to model ``name``; never raises for a model
    that fails to answer. ``LookupError`` when no such model is configured."""
    from deerflow.models.factory import create_chat_model

    if app_config is None:
        from deerflow.config.app_config import get_app_config

        app_config = get_app_config()
    if app_config.get_model_config(name) is None:
        raise LookupError(f"Model '{name}' not found")

    started = time.monotonic()

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        # A probe is not a conversation: keep it out of the tracing backends.
        model = create_chat_model(name, app_config=app_config, attach_tracing=False)
        reply = await asyncio.wait_for(model.ainvoke(PROBE_PROMPT), timeout=timeout)
    except TimeoutError:
        return ModelProbeResult(ok=False, latency_ms=_elapsed(), detail=f"Timed out after {timeout:.0f}s — is the endpoint running?", timed_out=True)
    except Exception as exc:
        return ModelProbeResult(ok=False, latency_ms=_elapsed(), detail=f"{type(exc).__name__}: {exc}")
    return ModelProbeResult(ok=True, latency_ms=_elapsed(), detail=str(getattr(reply, "content", reply)))
