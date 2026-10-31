/**
 * The Account section fetched `/api/v1/credits` and `/api/v1/referral` and
 * dereferenced numeric fields for `.toLocaleString()`; a payload with a
 * different shape threw in render and crashed the whole settings route.
 */
import { describe, expect, it } from "vitest";

import { isCredits, isReferral } from "@/core/credits/guards";

describe("isCredits", () => {
  const valid = {
    plan: "free",
    daily_limit: 1000,
    used: 10,
    remaining: 990,
    unlimited: false,
    request_status: null,
  };

  it("accepts the gateway shape", () => {
    expect(isCredits(valid)).toBe(true);
    expect(isCredits({ ...valid, request_status: "pending" })).toBe(true);
  });

  it("rejects partial or mistyped payloads", () => {
    expect(isCredits({ balance: 0, plan: "free" })).toBe(false);
    expect(isCredits({ ...valid, used: "10" })).toBe(false);
    expect(isCredits({ ...valid, daily_limit: Number.NaN })).toBe(false);
    expect(isCredits(null)).toBe(false);
  });
});

describe("isReferral", () => {
  it("accepts the gateway shape and rejects others", () => {
    expect(
      isReferral({ code: "ABC", referral_count: 2, bonus_daily_tokens: 500 }),
    ).toBe(true);
    expect(isReferral({ code: "ABC", referrals: 0 })).toBe(false);
    expect(
      isReferral({ code: 1, referral_count: 2, bonus_daily_tokens: 5 }),
    ).toBe(false);
  });
});
