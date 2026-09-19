/** GET /api/agents/registry — every agent Nova can run, with live queued/running counts. */
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export type AgentKind = "lead" | "subagent" | "custom" | "acp";

export interface RegistryAgent {
  id: string;
  kind: AgentKind;
  name: string;
  description: string;
  model: string | null;
  runner: "gateway" | "jobs";
  async_capable: boolean;
  queued: number;
  running: number;
}

export interface AgentsRegistry {
  async_enabled: boolean;
  agents: RegistryAgent[];
}

export async function fetchAgentsRegistry(): Promise<AgentsRegistry> {
  const res = await fetch(`${getBackendBaseURL()}/api/agents/registry`);
  if (!res.ok) throw new Error(`registry: HTTP ${res.status}`);
  const body = (await res.json()) as AgentsRegistry;
  return {
    async_enabled: Boolean(body.async_enabled),
    agents: (body.agents ?? []).filter(
      (a) => typeof a?.id === "string" && typeof a?.name === "string",
    ),
  };
}
