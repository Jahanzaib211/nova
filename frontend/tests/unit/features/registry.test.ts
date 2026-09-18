import { describe, expect, it } from "vitest";

import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import {
  FEATURE_MANIFESTS,
  settingsPages,
  isSettingsPageId,
} from "@/features/registry";
import { SETTINGS_PAGE_IDS } from "@/features/types";

/**
 * The feature registry is the one place a settings page is declared.
 * settings-dialog.tsx renders whatever is registered, so these invariants
 * are what keeps the dialog, the /workspace/settings route and the nav
 * menu's deep links in agreement.
 */
describe("feature registry", () => {
  it("registers every known settings page exactly once, with unique orders", () => {
    const pages = settingsPages();
    expect(pages.map((p) => p.id).sort()).toEqual(
      [...SETTINGS_PAGE_IDS].sort(),
    );
    expect(new Set(pages.map((p) => p.order)).size).toBe(pages.length);
  });

  it("orders pages ascending and keeps appearance near the top", () => {
    const orders = settingsPages().map((p) => p.order);
    expect(orders).toEqual([...orders].sort((a, b) => a - b));
    expect(
      settingsPages().findIndex((p) => p.id === "appearance"),
    ).toBeLessThan(3);
  });

  it("has a label in both locales and a page component for each entry", () => {
    for (const page of settingsPages()) {
      expect(page.label(enUS), page.id).toBeTruthy();
      expect(page.label(zhCN), page.id).toBeTruthy();
      expect(typeof page.Page).not.toBe("undefined");
    }
  });

  it("feature ids are unique", () => {
    const ids = FEATURE_MANIFESTS.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("isSettingsPageId guards hash fragments", () => {
    expect(isSettingsPageId("voice")).toBe(true);
    expect(isSettingsPageId("nope")).toBe(false);
  });
});
