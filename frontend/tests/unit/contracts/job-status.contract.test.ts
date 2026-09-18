import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  JOB_EVENT_TYPES,
  JOB_STATUSES,
  TERMINAL_JOB_STATUSES,
  isTerminalJobStatus,
} from "@/features/jobs/types";

interface ContractFile {
  statuses: string[];
  terminal_statuses: string[];
  event_types: string[];
  progress_schema: {
    properties: { pct: { minimum: number; maximum: number } };
  };
}

// The frontend package is ESM, so resolve the shared fixture from the module URL.
const CONTRACT_PATH = fileURLToPath(
  new URL("../../../../contracts/job_status_contract.json", import.meta.url),
);
const CONTRACT: ContractFile = JSON.parse(
  readFileSync(CONTRACT_PATH, "utf-8"),
) as ContractFile;

describe("job status contract", () => {
  it("statuses match the shared fixture, in order", () => {
    expect([...JOB_STATUSES]).toEqual(CONTRACT.statuses);
  });

  it("terminal statuses match the shared fixture", () => {
    expect(new Set(TERMINAL_JOB_STATUSES)).toEqual(
      new Set(CONTRACT.terminal_statuses),
    );
    for (const status of CONTRACT.statuses) {
      expect(isTerminalJobStatus(status)).toBe(
        CONTRACT.terminal_statuses.includes(status),
      );
    }
  });

  it("event types match the shared fixture, in order", () => {
    expect([...JOB_EVENT_TYPES]).toEqual(CONTRACT.event_types);
  });

  it("progress pct is bounded 0..100", () => {
    expect(CONTRACT.progress_schema.properties.pct.minimum).toBe(0);
    expect(CONTRACT.progress_schema.properties.pct.maximum).toBe(100);
  });
});
