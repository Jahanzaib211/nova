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
