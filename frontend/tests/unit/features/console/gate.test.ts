/**
 * The Runtimes settings section (Settings › Agents › Runtimes).
 *
 * `features.runtimes` comes from `/api/runtime/capabilities`, and every
 * feature flag reads as OFF until that request answers (see
 * `useFeatureFlagsState`). A page that renders "runtimes.enabled is false
 * in config.yaml" during the request states something the next tick
 * contradicts — the gate has to tell "off" from "not asked yet".
 *
 * The email page gates the same way (`features/email-marketing/gate.ts`);
 * this is the same rule for the agents section.
 */
import { describe, expect, it } from "vitest";

import { parseFeatureFlags } from "@/core/runtime/feature-flags";
import { runtimesGate } from "@/features/console/gate";
import { settingsPages } from "@/features/registry";

describe("runtimes gate", () => {
  it("is disabled when the server says the feature is off", () => {
    expect(runtimesGate({ runtimes: false })).toBe("disabled");
  });

  it("is enabled when the server says the feature is on", () => {
    expect(runtimesGate({ runtimes: true })).toBe("enabled");
  });

  it("reads the flag through the same merge the app uses", () => {
    expect(runtimesGate(parseFeatureFlags("runtimes"))).toBe("enabled");
    expect(runtimesGate(parseFeatureFlags(undefined))).toBe("disabled");
    expect(runtimesGate(parseFeatureFlags("all"))).toBe("enabled");
  });

  it("does not claim 'not enabled' before the server has answered", () => {
    expect(runtimesGate({ runtimes: false }, true)).toBe("loading");
    expect(runtimesGate({ runtimes: true }, true)).toBe("loading");
  });
});

describe("the settings agents section", () => {
  it("is the Runtimes page, and it is not a copy of /workspace/agents", () => {
    // The registry lives on /workspace/agents; the settings rail links to it
    // instead of repeating it (the changelog calls a copy here a defect).
    const page = settingsPages().find((p) => p.id === "runtimes");
    expect(page).toBeDefined();
    expect(page?.group).toBe("agents");
    // No settings page is allowed to be a second copy of the registry.
    // ("agents" is deliberately not a SettingsPageId — the cast keeps the
    // assertion honest if a page with that id is ever added.)
    expect(settingsPages().some((p) => (p.id as string) === "agents")).toBe(
      false,
    );
  });
});
