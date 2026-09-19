import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { isAcpUpdateEvent } from "@/core/threads/acp-transcript";

/**
 * Frontend half of `contracts/custom_events_contract.json`. `contracts/README.md`
 * has listed this file since the contract was written; it did not exist, so
 * the contract was documentation, not a contract. It now pins two things:
 * every event type the stream handler switches on is declared by the
 * contract, and the runtime guard for the newest event accepts exactly the
 * contract's shape.
 */
const contract = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL(
        "../../../../../contracts/custom_events_contract.json",
        import.meta.url,
      ),
    ),
    "utf-8",
  ),
) as {
  required: string[];
  properties: Record<
    string,
    {
      required: string[];
      properties: Record<string, { enum?: string[]; const?: string }>;
    }
  >;
};

/** The `event.type` values `useThreadStream.onCustomEvent` handles. */
const HANDLED = [
  "task_started",
  "task_completed",
  "task_failed",
  "task_timed_out",
  "task_running",
  "task_activity",
  "safety_termination",
  "llm_retry",
  "task_progress",
  "verify_result",
  "llm_error",
  "acp_update",
];

describe("custom events contract (frontend)", () => {
  it("declares every event type the stream handler switches on", () => {
    for (const type of HANDLED) {
      expect(contract.required, type).toContain(type);
      expect(contract.properties[type]?.properties.type?.const).toBe(type);
    }
  });

  it("acp_update: the runtime guard matches the contract", () => {
    const spec = contract.properties.acp_update!;
    expect(spec.required).toEqual([
      "type",
      "agent",
      "session_id",
      "kind",
      "delta",
    ]);
    expect(spec.properties.kind?.enum).toEqual(["text", "status"]);
    const sample = {
      type: "acp_update",
      agent: "claude_code",
      session_id: "s",
      kind: "text",
      delta: "x",
    };
    expect(isAcpUpdateEvent(sample)).toBe(true);
    for (const key of spec.required) {
      const missing: Record<string, unknown> = { ...sample };
      delete missing[key];
      expect(isAcpUpdateEvent(missing), `without ${key}`).toBe(false);
    }
  });
});
