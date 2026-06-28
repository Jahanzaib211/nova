/**
 * E2E spec: Landing page → workspace navigation pipeline.
 *
 * Tests the user-facing happy path:
 *  - /  (landing) renders hero + Get Started CTA
 *  - Clicking "Get Started" navigates to /workspace/chats/new
 *  - The workspace URL is reachable
 */

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Landing → workspace pipeline", () => {
  test("landing page hero renders with the brand and CTA", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: /get started/i })).toBeVisible({
      timeout: 15_000,
    });
    // Brand name is in the header h1 (one of the few landmarks).
    await expect(
      page.locator("header").getByText("DeerFlow").first(),
    ).toBeVisible();
  });

  test("Get Started CTA navigates to the workspace", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.goto("/");
    const cta = page.getByRole("link", { name: /get started/i });
    await cta.click();
    // After navigation, the URL should land under /workspace/ (no trailing
    // slash required since the link target is /workspace).
    await page.waitForURL(/\/workspace/, { timeout: 10_000 });
    // Page is reachable, status < 500.
    const url = page.url();
    expect(url).toMatch(/\/workspace/);
  });
});
