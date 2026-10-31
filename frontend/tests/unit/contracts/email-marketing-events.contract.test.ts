import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CAMPAIGN_STATUSES,
  CONTACT_STATUSES,
  EMAIL_EVENT_TYPES,
  SUPPRESSION_REASONS,
  TERMINAL_CAMPAIGN_STATUSES,
} from "@/features/email-marketing/types";

interface ContractFile {
  event_types: string[];
  campaign_statuses: string[];
  terminal_campaign_statuses: string[];
  contact_statuses: string[];
  suppression_reasons: string[];
}

const CONTRACT_PATH = fileURLToPath(
  new URL(
    "../../../../contracts/email_marketing_events_contract.json",
    import.meta.url,
  ),
);
const CONTRACT: ContractFile = JSON.parse(
  readFileSync(CONTRACT_PATH, "utf-8"),
) as ContractFile;

describe("email marketing events contract", () => {
  it("event types match the shared fixture, in order", () => {
    expect([...EMAIL_EVENT_TYPES]).toEqual(CONTRACT.event_types);
  });

  it("campaign statuses match the shared fixture", () => {
    expect([...CAMPAIGN_STATUSES]).toEqual(CONTRACT.campaign_statuses);
    expect(new Set(TERMINAL_CAMPAIGN_STATUSES)).toEqual(
      new Set(CONTRACT.terminal_campaign_statuses),
    );
  });

  it("contact statuses and suppression reasons match the shared fixture", () => {
    expect([...CONTACT_STATUSES]).toEqual(CONTRACT.contact_statuses);
    expect([...SUPPRESSION_REASONS]).toEqual(CONTRACT.suppression_reasons);
  });
});
