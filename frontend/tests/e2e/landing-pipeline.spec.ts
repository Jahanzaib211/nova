/**
 * E2E spec: Landing page → workspace navigation pipeline.
 *
 * Tests the user-facing happy path:
 *  - /  (landing) renders the hero + Start building CTA
 *  - Clicking "Start building" navigates to /workspace
 */

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Landing → workspace pipeline", () => {
  test("landing page hero renders with the brand and CTA", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("link", { name: "Start building" }).first(),
    ).toBeVisible({ timeout: 15_000 });
    // Brand name is in the header h1 (one of the few landmarks).
    await expect(
      page.locator("header").getByText("Nova").first(),
    ).toBeVisible();
  });

  test("Start building CTA navigates to the workspace", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.goto("/");
    const cta = page.getByRole("link", { name: "Start building" }).first();
    await cta.click();
    // The link target is /workspace; the app may normalise it to a chat URL.
    await page.waitForURL(/\/workspace/, { timeout: 10_000 });
    const url = page.url();
    expect(url).toMatch(/\/workspace/);
  });
});
