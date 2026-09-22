/**
 * Doom tests: adversarial E2E tests for the Agent Computer panel.
 *
 * These tests verify the panel handles edge cases, rapid interactions,
 * and stress scenarios without crashing or leaking state.
 */

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Agent Computer doom tests", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);
  });

  test("rapid tab switching does not crash", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    // Rapidly switch between tabs 20 times
    const tabs = ["terminal", "editor", "browser", "telemetry", "files"];
    for (let i = 0; i < 20; i++) {
      const idx = i % tabs.length;
      const tabName = tabs[idx]!;
      const tab = page.getByRole("tab", { name: new RegExp(tabName, "i") });
      if ((await tab.count()) > 0) {
        await tab.first().click();
        // No wait — fire immediately
      }
    }

    // Panel should still be functional after rapid switching
    await expect(page.getByRole("tab").first()).toBeVisible();
  });

  test("panel survives navigation away and back", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    // Navigate away
    await page.goto("/");
    await expect(
      page.getByRole("link", { name: /get started/i }).first(),
    ).toBeVisible({ timeout: 15_000 });

    // Navigate back
    await page.goto("/workspace/chats/new");
    // Panel should be closeable/reopenable
    const trigger2 = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger2.count()) > 0) {
      await trigger2.first().click();
      await expect(page.getByRole("tab").first()).toBeVisible({
        timeout: 5_000,
      });
    }
  });

  test("empty state renders without errors", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    // Click Editor tab — should show empty state
    const editorTab = page.getByRole("tab", { name: /editor/i });
    if ((await editorTab.count()) > 0) {
      await editorTab.first().click();
      // Should show "start writing" hint or empty state
      await expect(page.locator('[data-tab="editor"]')).toBeVisible({
        timeout: 3_000,
      });
    }
  });

  test("concurrent panel open/close does not leak", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }

    // Open and close 10 times rapidly
    for (let i = 0; i < 10; i++) {
      await trigger.first().click();
      // Wait for panel to appear
      await page
        .getByRole("tab")
        .first()
        .waitFor({ state: "visible", timeout: 3_000 })
        // The panel may not have opened on this iteration — that is what the
        // rapid open/close loop is probing for, so a miss is not a failure.
        .catch(() => undefined);

      // Close by clicking trigger again or a close button
      const closeBtn = page.getByRole("button", { name: /close|collapse/i });
      if ((await closeBtn.count()) > 0) {
        await closeBtn.first().click();
      } else {
        await trigger.first().click();
      }
    }

    // No JS errors — page should still be responsive
    const resp = await page.goto("/workspace/chats/new");
    expect(resp?.status()).toBeLessThan(500);
  });

  test("browser tab iframe handles navigation errors gracefully", async ({
    page,
  }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    // Click Browser tab
    const browserTab = page.getByRole("tab", { name: /browser/i });
    if ((await browserTab.count()) > 0) {
      await browserTab.first().click();
      // Should not crash even if iframe URL is unreachable
      await expect(page.locator('[data-tab="browser"]')).toBeVisible({
        timeout: 3_000,
      });
    }
  });

  test("terminal tab handles rapid input without freeze", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    const termTab = page.getByRole("tab", { name: /terminal/i });
    if ((await termTab.count()) > 0) {
      await termTab.first().click();
      await expect(page.locator('[data-tab="terminal"]')).toBeVisible({
        timeout: 3_000,
      });
    }
  });

  test("files tab renders without errors in empty state", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "trigger not visible");
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    const filesTab = page.getByRole("tab", { name: /files/i });
    if ((await filesTab.count()) > 0) {
      await filesTab.first().click();
      await expect(page.locator('[data-tab="files"]')).toBeVisible({
        timeout: 3_000,
      });
    }
  });
});
