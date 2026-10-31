"""Derive lead-agent tools from the capability registry.

One :class:`~langchain_core.tools.StructuredTool` per operation that is
``harness=True``, of a tool-safe kind, and whose module's ``nova:<module>``
group is enabled in ``config.yaml`` ``tool_groups`` — the same allow-list
mechanism ``web`` / ``bash`` already use, so operators gate Nova's own
capabilities the way they gate everything else.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import BaseTool, StructuredTool

from deerflow.capabilities.registry import CapabilityRegistry
from deerflow.capabilities.types import OpContext, Operation
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

GROUP_PREFIX = "nova:"


def group_for(op: Operation) -> str:
    return f"{GROUP_PREFIX}{op.module_id}"


def _make_tool(registry: CapabilityRegistry, op: Operation) -> BaseTool:
    async def _run(**kwargs) -> str:
        ctx = OpContext(user_id=get_effective_user_id(), is_admin=False, surface="harness")
        try:
            result = await registry.invoke(op.name, ctx, kwargs)
        except Exception as exc:  # the model needs the reason, not a traceback
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        return json.dumps(result, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        coroutine=_run,
        name=op.tool_name,
        description=f"[{op.kind}] {op.description}",
        args_schema=op.input,
    )


def build_capability_tools(registry: CapabilityRegistry, *, enabled_flags: set[str], groups: set[str]) -> list[BaseTool]:
    tools: list[BaseTool] = []
    # The registry's policy decides which ops a model may hold (not
    # admin-only, a harness-safe kind, flag on); invoke re-checks it per call.
    probe = OpContext(user_id="", is_admin=False, surface="harness")
    for op in registry.permitted(probe, enabled_flags=enabled_flags):
        if group_for(op) not in groups:
            continue
        tools.append(_make_tool(registry, op))
    if tools:
        logger.info("Including %d capability tool(s): %s", len(tools), [t.name for t in tools])
    return tools
