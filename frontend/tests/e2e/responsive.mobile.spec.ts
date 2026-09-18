import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Runs under the `mobile-chrome` project (see playwright.config.ts), which
 * supplies a real phone viewport. Guards the responsive behaviour that the
 * Desktop Chrome project cannot exercise.
 */

/**
 * Nothing may extend past the right edge of the viewport.
 *
 * Polled, not sampled: a section still streaming in its content can be wider
 * for a frame or two, and under parallel workers a single sample landed in
 * that window. The steady state is what must fit.
 */
async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const doc = document.documentElement;
          return doc.scrollWidth - doc.clientWidth;
        }),
      { timeout: 5_000 },
    )
    // Sub-pixel rounding shows up as a 1px diff on some builds.
    .toBeLessThanOrEqual(1);
}

test.describe("landing page on mobile", () => {
  test("collapses the nav behind a menu button and does not overflow", async ({
    page,
  }) => {
    await page.goto("/");

    // The header's inline desktop links are hidden below md (scoped to the
    // header — the page has other <nav> landmarks, e.g. the footer).
    const header = page.locator("header").first();
    await expect(
      header.getByRole("link", { name: /^(docs|文档)$/i }),
    ).toBeHidden();

    const menu = page.getByRole("button", { name: /menu|菜单/i });
    await expect(menu).toBeVisible({ timeout: 15_000 });

    await menu.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(
      page.getByRole("dialog").getByRole("link", { name: /docs|文档/i }),
    ).toBeVisible();

    await expectNoHorizontalOverflow(page);
  });
});

test.describe("workspace on mobile", () => {
  test("renders the chat full width without horizontal overflow", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");

    const input = page.getByRole("textbox").first();
    await expect(input).toBeVisible({ timeout: 15_000 });

    const viewportWidth = page.viewportSize()?.width ?? 412;
    // The composer remounts once thread state settles; re-query rather than
    // trusting a handle taken before the remount (boundingBox() came back
    // null under parallel workers).
    await expect
      .poll(async () => {
        const box = await page.getByRole("textbox").first().boundingBox();
        return box ? box.x + box.width : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(viewportWidth + 1);

    await expectNoHorizontalOverflow(page);
  });

  test("the Agent's Computer can be opened and closed again on a phone", async ({
    page,
  }) => {
    // A write_file tool call auto-opens the Agent's Computer.
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "00000000-0000-0000-0000-0000000031aa",
          title: "mobile computer panel",
          messages: [
            {
              type: "human",
              id: "m-h",
              content: [{ type: "text", text: "Create a report" }],
            },
            {
              type: "ai",
              id: "m-a",
              content: "",
              tool_calls: [
                {
                  id: "m-tc",
                  name: "write_file",
                  args: {
                    description: "Writing report artifact",
                    path: "/artifact-fixtures/report.html",
                    content: "<!doctype html><html><body>x</body></html>",
                  },
                },
              ],
            },
          ],
        },
      ],
    });

    await page.goto("/workspace/chats/00000000-0000-0000-0000-0000000031aa");

    // Panel auto-opens, so the switcher appears with a Computer tab.
    // Scoped to the mobile switcher: the page header's toggle and the panel's
    // close button carry the same name.
    const switcher = page.getByRole("navigation", { name: /panels|面板/i });
    const computerTab = switcher.getByRole("button", {
      name: /^(agent's computer|智能体电脑)$/i,
    });
    await expect(computerTab).toBeVisible({ timeout: 15_000 });

    // Switching back to Chat, and then to the panel again, must work without a
    // reload — the panel takes the whole screen, so the switcher is the only
    // way back.
    const chatTab = switcher.getByRole("button", { name: /^(chat|对话)$/i });
    await chatTab.click();
    await expect(page.getByRole("textbox").first()).toBeVisible();

    await computerTab.click();

    // Closing must actually dismiss it — and take its tab with it.
    const close = page.getByRole("button", {
      name: /close agent's computer|关闭.*电脑/i,
    });
    await expect(close.first()).toBeVisible();
    await close.first().click();

    await expect(computerTab).toHaveCount(0);

    // …and the page-header toggle must still be there to re-open it, without
    // a reload. It is icon-only, so this also pins its accessible name.
    const reopen = page.getByRole("button", {
      name: /^(agent's computer|智能体电脑)$/i,
    });
    await expect(reopen).toBeVisible();

    await expectNoHorizontalOverflow(page);
  });

  test("hides the panel switcher until a second surface is open", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");
    await expect(page.getByRole("textbox").first()).toBeVisible({
      timeout: 15_000,
    });

    // With only the chat open there is nothing to switch between, so the tab
    // strip should not be taking vertical space.
    await expect(
      page.getByRole("button", { name: /^(Chat|对话)$/ }),
    ).toHaveCount(0);
  });
});

test.describe("settings route on mobile", () => {
  // Regressions caught by the 2026-09-18 visual baseline: the section grid
  // had no explicit column below md, so any wide content (the Models page's
  // preset buttons) pushed the whole document to 1297px; and the page had no
  // sidebar trigger, so a phone had no way back to the chat list.
  const SECTIONS = ["models", "tools", "account", "memory"] as const;

  for (const section of SECTIONS) {
    test(`#${section} does not overflow the viewport`, async ({ page }) => {
      mockLangGraphAPI(page);
      await page.goto(`/workspace/settings#${section}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({
        timeout: 15_000,
      });
      await page.waitForTimeout(500);
      await expectNoHorizontalOverflow(page);
    });
  }

  test("offers the sidebar trigger", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.goto("/workspace/settings#appearance");
    const trigger = page.getByRole("button", { name: /toggle sidebar/i });
    await expect(trigger).toBeVisible({ timeout: 15_000 });
    await trigger.click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });
});

test.describe("chat header on mobile", () => {
  test("keeps a long thread title on one line", async ({ page }) => {
    const threadId = "00000000-0000-0000-0000-0000000031bb";
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: threadId,
          title:
            "A deliberately long thread title that used to wrap onto a second line in the header",
          updated_at: "2026-09-18T12:00:00Z",
        },
      ],
    });
    await page.goto(`/workspace/chats/${threadId}`);
    const title = page.locator("header span.truncate").first();
    await expect(title).toBeVisible({ timeout: 15_000 });
    const box = await title.boundingBox();
    expect(box).not.toBeNull();
    // One line of 14px text is ~20px tall; two lines would be ~40px.
    expect(box!.height).toBeLessThan(30);
  });
});
