import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Runs under the `mobile-chrome` project (see playwright.config.ts), which
 * supplies a real phone viewport. Guards the responsive behaviour that the
 * Desktop Chrome project cannot exercise.
 */

/** Nothing may extend past the right edge of the viewport. */
async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  // Sub-pixel rounding shows up as a 1px diff on some builds.
  expect(overflow).toBeLessThanOrEqual(1);
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
    const box = await input.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewportWidth + 1);

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
