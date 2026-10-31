/** Presentation mapping for integration health — the only place a status picks a colour. */
import type {
  IntegrationHealthItem,
  IntegrationKind,
  IntegrationStatus,
} from "./types";

export type HealthTone = "success" | "warning" | "destructive" | "muted";

export function toneFor(status: IntegrationStatus): HealthTone {
  switch (status) {
    case "healthy":
      return "success";
    case "degraded":
    case "unknown":
      return "warning";
    case "down":
      return "destructive";
    case "disabled":
      return "muted";
  }
}

export const DOT_CLASS: Record<HealthTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  muted: "bg-muted-foreground/40",
};

export const TEXT_CLASS: Record<HealthTone, string> = {
  success: "text-success",
  warning: "text-warning-foreground dark:text-warning",
  destructive: "text-destructive",
  muted: "text-muted-foreground",
};

export const GROUP_IDS = ["models", "business", "web", "other"] as const;
export type GroupId = (typeof GROUP_IDS)[number];

/** Card groups, in display order. Kinds not listed fall into the last group. */
export const KIND_GROUPS: ReadonlyArray<{
  id: GroupId;
  kinds: readonly IntegrationKind[];
}> = [
  { id: "models", kinds: ["llm_gateway"] },
  { id: "business", kinds: ["mail", "crm", "helpdesk"] },
  { id: "web", kinds: ["search", "crawler", "browser"] },
  { id: "other", kinds: ["database"] },
];

/**
 * Kinds with their own settings page (Tools for MCP servers, Skills for
 * skills, Runtimes for agent gateways and ACP agents). Listing them here too
 * made Integrations a second, read-only copy of those pages, so they are left
 * out of the health view entirely.
 */
export const MANAGED_ELSEWHERE: readonly IntegrationKind[] = [
  "mcp_server",
  "skill",
  "agent_gateway",
  "acp_agent",
];

export function visibleIntegrations(
  items: IntegrationHealthItem[],
): IntegrationHealthItem[] {
  return items.filter((item) => !MANAGED_ELSEWHERE.includes(item.kind));
}

export function groupIntegrations(
  items: IntegrationHealthItem[],
): Array<{ id: GroupId; items: IntegrationHealthItem[] }> {
  const out = KIND_GROUPS.map((g) => ({
    id: g.id,
    items: [] as IntegrationHealthItem[],
  }));
  const fallback = out[out.length - 1]!;
  for (const item of visibleIntegrations(items)) {
    const group = KIND_GROUPS.findIndex((g) => g.kinds.includes(item.kind));
    (group >= 0 ? out[group]! : fallback).items.push(item);
  }
  return out.filter((g) => g.items.length > 0);
}

/** Counts by status, for the summary line. */
export function summarize(
  items: IntegrationHealthItem[],
): Record<IntegrationStatus, number> {
  const counts: Record<IntegrationStatus, number> = {
    healthy: 0,
    degraded: 0,
    down: 0,
    unknown: 0,
    disabled: 0,
  };
  for (const item of items) counts[item.status] += 1;
  return counts;
}
