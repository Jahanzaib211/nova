/**
 * The Runtime settings page renders `"undefined: ?"` and `"undefineds"` when
 * the gateway omits a field (older gateway, feature section disabled): the
 * page only checks `!== null`. Normalising the payload makes every field
 * present and null-able, so the page's existing null checks are sufficient.
 */
import { describe, expect, it } from "vitest";

import { normalizeRuntimeConfig } from "@/core/api/runtime-config";

describe("normalizeRuntimeConfig", () => {
  it("fills every field of a sparse payload with null/false/0", () => {
    const cfg = normalizeRuntimeConfig({
      summarization: { enabled: true },
      subagents: { enabled: true, max_concurrent: 3 },
      guardrails: { enabled: true },
    });
    expect(cfg).toEqual({
      summarization: {
        enabled: true,
        model_name: null,
        trigger_type: null,
        trigger_value: null,
        keep_type: null,
        keep_value: null,
      },
      subagents: {
        default_timeout_seconds: null,
        max_turns: null,
        custom_agents_count: 0,
      },
      guardrails: {
        enabled: true,
        fail_closed: false,
        passport: null,
        provider_class: null,
      },
    });
  });

  it("keeps a complete payload intact", () => {
    const full = {
      summarization: {
        enabled: false,
        model_name: "m",
        trigger_type: "tokens",
        trigger_value: 1000,
        keep_type: "messages",
        keep_value: 5,
      },
      subagents: {
        default_timeout_seconds: 900,
        max_turns: 20,
        custom_agents_count: 2,
      },
      guardrails: {
        enabled: true,
        fail_closed: true,
        passport: "p",
        provider_class: "X",
      },
    };
    expect(normalizeRuntimeConfig(full)).toEqual(full);
  });

  it("tolerates junk (null, strings, wrong types)", () => {
    expect(normalizeRuntimeConfig(null).summarization.enabled).toBe(false);
    expect(normalizeRuntimeConfig("x").subagents.custom_agents_count).toBe(0);
    expect(
      normalizeRuntimeConfig({ subagents: { max_turns: "20" } }).subagents
        .max_turns,
    ).toBeNull();
  });
});
