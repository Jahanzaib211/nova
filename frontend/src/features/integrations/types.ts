/**
 * Integrations health vocabulary and item shape.
 *
 * Pinned to `contracts/integrations_health_contract.json` by
 * `tests/unit/contracts/integrations-health.contract.test.ts`; the backend
 * (`deerflow.integrations.health`) and `scripts/inventory.py` pin the same
 * file.
 */

export const INTEGRATION_STATUSES = [
  "healthy",
  "degraded",
  "down",
  "unknown",
  "disabled",
] as const;
export type IntegrationStatus = (typeof INTEGRATION_STATUSES)[number];

export const INTEGRATION_KINDS = [
  "mcp_server",
  "skill",
  "acp_agent",
  "llm_gateway",
  "mail",
  "crm",
  "helpdesk",
  "search",
  "crawler",
  "browser",
  "database",
  "agent_gateway",
] as const;
export type IntegrationKind = (typeof INTEGRATION_KINDS)[number];

export interface IntegrationHealthItem {
  id: string;
  kind: IntegrationKind;
  display_name: string;
  endpoint?: string | null;
  status: IntegrationStatus;
  latency_ms?: number | null;
  checked_at: string | null;
  detail?: string | null;
  capabilities?: string[];
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Runtime guard for `GET /api/integrations` items (the contract's `item_schema`). */
export function isIntegrationHealthItem(
  value: unknown,
): value is IntegrationHealthItem {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    isNonEmptyString(v.id) &&
    (INTEGRATION_KINDS as readonly string[]).includes(v.kind as string) &&
    isNonEmptyString(v.display_name) &&
    (INTEGRATION_STATUSES as readonly string[]).includes(v.status as string) &&
    "checked_at" in v &&
    (v.checked_at === null || typeof v.checked_at === "string")
  );
}
