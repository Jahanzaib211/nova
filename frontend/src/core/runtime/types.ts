/** Types for /api/runtime/capabilities response.
 *
 * Mirrors backend/app/gateway/routers/capabilities.py CapabilitiesResponse.
 * Adding a new field server-side: add it here as optional, never required.
 */

export interface SkillSummary {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
}

export interface ToolSummary {
  name: string;
  description: string;
}

export interface HookSummary {
  name: string;
  kind: string; // "middleware" | "event" | "callback"
}

export interface SubagentSummary {
  name: string;
  description: string;
}

export interface CircuitEntry {
  thread_id: string;
  state: string; // "closed" | "open" | "half_open"
}

export interface ServerInfo {
  process: string;
  version: string;
  pid: number;
}

export interface CapabilitiesResponse {
  skills: SkillSummary[];
  tools: ToolSummary[];
  hooks: HookSummary[];
  subagents: SubagentSummary[];
  circuits: CircuitEntry[];
  server: ServerInfo;
}
