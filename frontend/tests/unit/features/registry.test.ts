import { describe, expect, it } from "vitest";

import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import {
  FEATURE_MANIFESTS,
  settingsGroups,
  settingsPages,
  isSettingsPageId,
} from "@/features/registry";
import { SETTINGS_GROUPS, SETTINGS_PAGE_IDS } from "@/features/types";

/**
 * The feature registry is the one place a settings page is declared.
 * settings-dialog.tsx renders whatever is registered, so these invariants
 * are what keeps the dialog, the /workspace/settings route and the nav
 * menu's deep links in agreement.
 */
describe("feature registry", () => {
  it("registers every known settings page exactly once, with unique orders per group", () => {
    const pages = settingsPages();
    expect(pages.map((p) => p.id).sort()).toEqual(
      [...SETTINGS_PAGE_IDS].sort(),
    );
    for (const { group, pages: inGroup } of settingsGroups(pages)) {
      expect(new Set(inGroup.map((p) => p.order)).size, group).toBe(
        inGroup.length,
      );
    }
  });

  it("orders pages by group then order, and keeps appearance near the top", () => {
    const pages = settingsPages();
    const rank = (p: (typeof pages)[number]) =>
      SETTINGS_GROUPS.indexOf(p.group ?? "general") * 1000 + p.order;
    const ranks = pages.map(rank);
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    expect(pages.findIndex((p) => p.id === "appearance")).toBeLessThan(3);
  });

  it("buckets pages into the rail groups in order, omitting empty groups", () => {
    const groups = settingsGroups();
    const order = groups.map((g) => g.group);
    expect(order).toEqual(SETTINGS_GROUPS.filter((g) => order.includes(g)));
    expect(
      groups.find((g) => g.group === "connections")?.pages.map((p) => p.id),
    ).toContain("gateway");
    expect(
      groups.find((g) => g.group === "privacy")?.pages.map((p) => p.id),
    ).toEqual(["secrets"]);
    for (const g of SETTINGS_GROUPS) {
      expect(enUS.settings.groups[g], g).toBeTruthy();
      expect(zhCN.settings.groups[g], g).toBeTruthy();
    }
  });

  it("admin-only pages are declared, not implied", () => {
    const admin = settingsPages()
      .filter((p) => p.adminOnly)
      .map((p) => p.id);
    expect(admin).toEqual(["secrets"]);
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
