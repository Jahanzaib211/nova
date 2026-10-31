import { describe, expect, it } from "vitest";

import { parseFeatureFlags } from "@/core/runtime/feature-flags";
import { emailGate } from "@/features/email-marketing/gate";
import { navItems, settingsPages } from "@/features/registry";

/**
 * The email marketing UI is gated on the server-declared
 * `email_marketing` flag (config.yaml: email_marketing.enabled). While it is
 * off the gateway does not mount /api/em, so the page must not render the
 * campaigns/lists/contacts/templates/suppressions panels at all.
 */
describe("email marketing gate", () => {
  it("is disabled when the server says the feature is off", () => {
    expect(emailGate({ email_marketing: false })).toBe("disabled");
  });

  it("is enabled when the server says the feature is on", () => {
    expect(emailGate({ email_marketing: true })).toBe("enabled");
  });

  it("reads the flag through the same merge the app uses", () => {
    // The env override can switch the feature on for local work; the server
    // flag is merged on top of it (mergeFeatureFlags).
    expect(emailGate(parseFeatureFlags("email_marketing"))).toBe("enabled");
    expect(emailGate(parseFeatureFlags(undefined))).toBe("disabled");
    expect(emailGate(parseFeatureFlags("all"))).toBe("enabled");
  });

  it("does not claim 'not enabled' before the server has answered", () => {
    // Every flag reads as off until /api/runtime/capabilities responds, so
    // a page that showed the disabled hint during the request would say
    // something the next tick contradicts.
    expect(emailGate({ email_marketing: false }, true)).toBe("loading");
    expect(emailGate({ email_marketing: true }, true)).toBe("loading");
  });
});

describe("email surfaces are flagged", () => {
  it("hides the sidebar entry while the flag is off", () => {
    const entry = navItems().find((n) => n.href === "/workspace/email");
    expect(entry).toBeDefined();
    expect(entry?.flag).toBe("email_marketing");
  });

  it("hides the Settings › Email page while the flag is off", () => {
    const page = settingsPages().find((p) => p.id === "email");
    expect(page).toBeDefined();
    expect(page?.flag).toBe("email_marketing");
  });
});
