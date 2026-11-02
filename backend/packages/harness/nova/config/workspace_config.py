"""Configuration for the Workspace Intelligence Kernel (WIK)."""

from pydantic import BaseModel, Field


class WorkspaceIntelConfig(BaseModel):
    """Configuration for workspace intelligence (Phase C9/C10).

    When enabled, the gateway exposes ``/api/workspace/*`` endpoints that
    serve indexed workspace snapshots (projects, symbols, commands, graph)
    to the frontend panels. Disabled by default: the kernel stays dormant
    until the operator opts in, so enabling it is an explicit action rather
    than a side effect of deploying the code.
    """

    intelligence_enabled: bool = Field(
        default=False,
        description="Expose /api/workspace/* endpoints backed by the workspace intelligence kernel",
    )
