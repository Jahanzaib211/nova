import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  INTEGRATION_KINDS,
  INTEGRATION_STATUSES,
  isIntegrationHealthItem,
} from "@/features/integrations/types";

interface ContractFile {
  statuses: string[];
  kinds: string[];
  item_schema: { required: string[]; properties: Record<string, unknown> };
}

const CONTRACT_PATH = fileURLToPath(
  new URL(
    "../../../../contracts/integrations_health_contract.json",
    import.meta.url,
  ),
);
const CONTRACT: ContractFile = JSON.parse(
  readFileSync(CONTRACT_PATH, "utf-8"),
) as ContractFile;

describe("integrations health contract", () => {
  it("statuses match the shared fixture, in order", () => {
    expect([...INTEGRATION_STATUSES]).toEqual(CONTRACT.statuses);
  });

  it("kinds match the shared fixture, in order", () => {
    expect([...INTEGRATION_KINDS]).toEqual(CONTRACT.kinds);
  });

  it("type guard accepts an item shaped like the fixture and rejects a bad one", () => {
    const good = {
      id: "ollama",
      kind: "llm_gateway",
      display_name: "Ollama",
      endpoint: "http://host.docker.internal:11434",
      status: "healthy",
      latency_ms: 12.5,
      checked_at: "2026-09-18T00:00:00+00:00",
      detail: null,
      capabilities: ["chat"],
    };
    expect(Object.keys(good).sort()).toEqual(
      Object.keys(CONTRACT.item_schema.properties).sort(),
    );
    expect(isIntegrationHealthItem(good)).toBe(true);
    expect(isIntegrationHealthItem({ ...good, status: "on-fire" })).toBe(false);
    expect(isIntegrationHealthItem({ ...good, kind: "toaster" })).toBe(false);
    for (const key of CONTRACT.item_schema.required) {
      const missing: Record<string, unknown> = { ...good };
      delete missing[key];
      expect(isIntegrationHealthItem(missing)).toBe(false);
    }
  });
});
