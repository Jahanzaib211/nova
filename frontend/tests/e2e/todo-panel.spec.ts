import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

/**
 * The to-do tab on the composer, measured in a real browser (2026-10-31).
 *
 * Its geometry broke twice in ways no unit test noticed: a collapsed strip
 * that showed half of the first row, and an expanded list that cut its last
 * row against the composer. These assertions are pixel measurements of the
 * rendered page, not class strings.
 */

const TODOS = [
  {
    content: "Probe sandbox environment and load frontend-design skill",
    status: "completed",
  },
  {
    content:
      "Delegate full site build to subagents (design system, sections, routes)",
    status: "completed",
  },
  {
    content: "Verify TypeScript + production build compile clean",
    status: "completed",
  },
  {
    content:
      "Restart dev server (single instance) and wire into Agent's Browser",
    status: "in_progress",
  },
  {
    content: "Run browser_check across all routes and fix any render errors",
    status: "pending",
  },
  { content: "Run dev_verify and deliver final site", status: "pending" },
];

test.describe("To-do tab", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "Northarc build" }],
    });
    const values = {
      title: "Northarc build",
      messages: [
        {
          type: "human",
          id: "h1",
          content: [{ type: "text", text: "Build the site" }],
        },
        { type: "ai", id: "a1", content: "On it." },
      ],
      artifacts: [],
      todos: TODOS,
    };
    // Registered after the shared mocks, so these win for this thread.
    await page.route("**/api/langgraph/threads/*/history", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            values,
            next: [],
            metadata: {},
            created_at: "2025-01-01T00:00:00Z",
            parent_config: null,
          },
        ]),
      }),
    );
    await page.route("**/api/langgraph/threads/*/state", (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ values, next: [] }),
          })
        : route.fallback(),
    );
    await page.addInitScript(() => window.localStorage.clear());
  });

  async function geometry(page: Page) {
    const tab = page
      .locator("button[aria-controls]")
      .filter({ hasText: "To-dos" })
      .locator("..");
    const composer = page.locator("[data-slot='input-group']").first();
    const t = (await tab.boundingBox())!;
    const c = (await composer.boundingBox())!;
    return { tab, t, c };
  }

  test("collapsed: one header row, flush on the composer, inset by its corner radius", async ({
    page,
  }) => {
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const header = page
      .locator("button[aria-controls]")
      .filter({ hasText: "To-dos" });
    await expect(header).toBeVisible({ timeout: 15_000 });
    if ((await header.getAttribute("aria-expanded")) === "true")
      await header.click();

    const { tab, t, c } = await geometry(page);
    expect(Math.abs(t.y + t.height - c.y)).toBeLessThanOrEqual(1);
    expect(Math.round(t.x - c.x)).toBe(16);
    expect(Math.round(c.x + c.width - (t.x + t.width))).toBe(16);
    await expect(header).toContainText("3/6");
    await expect(header).toContainText("Restart dev server");
    await expect(tab.locator("ol")).toHaveCount(0);
    await page.waitForTimeout(400);
    await tab.screenshot({ path: "test-results/todo-collapsed.png" });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(400);
    await tab.screenshot({ path: "test-results/todo-collapsed-dark.png" });
    await page
      .locator("[data-slot='input-group']")
      .first()
      .locator("..")
      .locator("..")
      .screenshot({ path: "test-results/todo-collapsed-composer.png" });
  });

  test("expanded: whole rows only, with the step in progress in view", async ({
    page,
  }) => {
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const header = page
      .locator("button[aria-controls]")
      .filter({ hasText: "To-dos" });
    await expect(header).toBeVisible({ timeout: 15_000 });
    if ((await header.getAttribute("aria-expanded")) !== "true")
      await header.click();

    const { tab, t, c } = await geometry(page);
    expect(Math.abs(t.y + t.height - c.y)).toBeLessThanOrEqual(1);

    const list = tab.locator("ol");
    const box = (await list.boundingBox())!;
    // 4 rows x 32px + 4px padding top and bottom.
    expect(Math.round(box.height)).toBe(136);

    const active = list.locator("li").nth(3);
    const a = (await active.boundingBox())!;
    expect(a.y).toBeGreaterThanOrEqual(box.y - 0.5);
    expect(a.y + a.height).toBeLessThanOrEqual(box.y + box.height + 0.5);
    await page.waitForTimeout(400);
    await tab.screenshot({ path: "test-results/todo-expanded.png" });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(400);
    await tab.screenshot({ path: "test-results/todo-expanded-dark.png" });
    await page
      .locator("[data-slot='input-group']")
      .first()
      .locator("..")
      .locator("..")
      .locator("..")
      .screenshot({ path: "test-results/todo-expanded-dark-full.png" });
    await page
      .locator("[data-slot='input-group']")
      .first()
      .locator("..")
      .locator("..")
      .screenshot({ path: "test-results/todo-expanded-composer.png" });
  });
});
