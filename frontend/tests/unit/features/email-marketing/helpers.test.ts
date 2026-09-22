import { describe, expect, it } from "vitest";

import { enUS, zhCN } from "@/core/i18n";
import { tabFromHash } from "@/features/email-marketing/pages/email-page";
import {
  CAMPAIGN_ACTIONS,
  campaignTone,
  progressPct,
  rate,
} from "@/features/email-marketing/status";
import { CAMPAIGN_STATUSES } from "@/features/email-marketing/types";

describe("campaign presentation", () => {
  it("gives every contract status a tone and an action set", () => {
    for (const s of CAMPAIGN_STATUSES) {
      expect(campaignTone(s)).toBeTruthy();
      expect(CAMPAIGN_ACTIONS[s]).toBeDefined();
    }
    expect(CAMPAIGN_ACTIONS.draft).toEqual(["send-now"]);
    expect(CAMPAIGN_ACTIONS.sending).toEqual(["pause", "cancel"]);
    expect(CAMPAIGN_ACTIONS.completed).toEqual([]);
  });

  it("never reports a rate of nothing", () => {
    expect(rate(3, 0)).toBeNull();
    expect(rate(undefined, undefined)).toBeNull();
    expect(rate(1, 3)).toBe(33.3);
  });

  it("derives delivery progress from sends, null before any recipients exist", () => {
    expect(progressPct(undefined)).toBeNull();
    expect(progressPct({ recipients: 0 })).toBeNull();
    expect(progressPct({ recipients: 10, queued: 5, sending: 1 })).toBe(40);
    expect(progressPct({ recipients: 4, queued: 0, sending: 0 })).toBe(100);
  });

  it("maps the URL hash to a tab, defaulting to campaigns", () => {
    expect(tabFromHash("#lists")).toBe("lists");
    expect(tabFromHash("")).toBe("campaigns");
    expect(tabFromHash("#nope")).toBe("campaigns");
  });
});

describe("email i18n", () => {
  const leaves = (node: unknown, prefix = ""): string[] =>
    typeof node === "string" || typeof node === "function"
      ? [prefix]
      : Object.entries(node as Record<string, unknown>).flatMap(([k, v]) =>
          leaves(v, prefix ? `${prefix}.${k}` : k),
        );
  it("has every key in both locales", () => {
    const en = leaves(enUS.features.email);
    const zh = new Set(leaves(zhCN.features.email));
    expect(en.length).toBeGreaterThan(100);
    for (const key of en) expect(zh.has(key), key).toBe(true);
  });
});
