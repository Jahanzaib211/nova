import { describe, expect, it } from "vitest";

import { parseFeatureFlags } from "@/core/runtime/feature-flags";

describe("parseFeatureFlags", () => {
  it("is all off by default", () => {
    expect(Object.values(parseFeatureFlags(undefined)).every((v) => !v)).toBe(
      true,
    );
  });
  it("turns on the named flags and ignores unknown names", () => {
    const f = parseFeatureFlags(" jobs, integrations ,bogus");
    expect(f.jobs).toBe(true);
    expect(f.integrations).toBe(true);
    expect(f.email_marketing).toBe(false);
  });
  it("'all' enables everything", () => {
    expect(Object.values(parseFeatureFlags("all")).every(Boolean)).toBe(true);
  });
});

describe("mergeFeatureFlags", () => {
  it("server flags turn features on; env can add but never remove", async () => {
    const { mergeFeatureFlags } = await import("@/core/runtime/feature-flags");
    const envFlags = parseFeatureFlags("integrations");
    const merged = mergeFeatureFlags(envFlags, { jobs: true });
    expect(merged.jobs).toBe(true);
    expect(merged.integrations).toBe(true);
    expect(merged.email_marketing).toBe(false);
    expect(mergeFeatureFlags(envFlags, undefined)).toEqual(envFlags);
  });
});
