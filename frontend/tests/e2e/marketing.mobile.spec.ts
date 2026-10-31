import { expect, test, type Page } from "@playwright/test";

/**
 * Marketing pages on a phone (runs in the `mobile-chrome` project).
 *
 * Both pages are long, grid-heavy marketing layouts. The failure mode this
 * guards is a single wide card or terminal pushing the document sideways and
 * giving the whole page a horizontal scrollbar.
 */

async function expectNoHorizontalOverflow(page: Page) {
  const measure = () =>
    page.evaluate(() => {
      const doc = document.documentElement;
      const vw = doc.clientWidth;
      const offenders = Array.from(document.querySelectorAll("body *"))
        .filter((el) => {
          const r = el.getBoundingClientRect();
          if (r.width <= 40 || r.right <= vw + 1 || el.closest("nav")) {
            return false;
          }
          // Decorative glows are deliberately larger than the viewport and
          // clipped by an ancestor; they cannot create a scrollbar.
          let parent = el.parentElement;
          while (parent && parent !== document.body) {
            const overflowX = getComputedStyle(parent).overflowX;
            if (overflowX === "hidden" || overflowX === "clip") {
              return false;
            }
            parent = parent.parentElement;
          }
          return true;
        })
        .slice(0, 5)
        .map(
          (el) =>
            `${el.tagName.toLowerCase()} right=${Math.round(el.getBoundingClientRect().right)} .${(el.getAttribute("class") ?? "").slice(0, 80)}`,
        );
      return { overflow: doc.scrollWidth - doc.clientWidth, offenders };
    });

  await expect
    .poll(async () => (await measure()).overflow, { timeout: 15_000 })
    .toBeLessThanOrEqual(1);
  const final = await measure();
  expect(final.offenders, "elements past the viewport edge").toEqual([]);
}

test.describe("landing page on a phone", () => {
  test("shows the kit hero and pricing teaser without overflowing", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /ai developer for companies that can't send their code to the cloud/i,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Start building" }).first(),
    ).toBeVisible();

    await page.locator("#pricing").scrollIntoViewIfNeeded();
    const pricing = page.locator("#pricing");
    await expect(pricing.getByText("$49", { exact: true })).toBeVisible();
    await expect(pricing.getByText("from $12,000")).toBeVisible();

    await expectNoHorizontalOverflow(page);
  });

  test("menu exposes Product, Security, Pricing and Docs", async ({ page }) => {
    await page.goto("/");

    const menu = page.getByRole("button", { name: /menu/i });
    await expect(menu).toBeVisible({ timeout: 15_000 });
    await menu.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // Let the sheet finish sliding in before measuring the layout.
    await page.waitForTimeout(400);
    for (const label of ["Product", "Security", "Pricing", "Docs"]) {
      await expect(
        dialog.getByRole("link", { name: label, exact: true }),
      ).toBeVisible();
    }

    await expectNoHorizontalOverflow(page);
  });
});

test.describe("pricing page on a phone", () => {
  test("stacks all four plans and stays inside the viewport", async ({
    page,
  }) => {
    await page.goto("/saas");

    // Buyer cards are articles too, so scope the count to plan cards.
    const plans = page.locator('article[aria-label$="plan"]');
    await expect(plans).toHaveCount(4);
    for (const name of ["Community", "Plus", "Enterprise", "Services"]) {
      await expect(
        page.getByRole("article", { name: `${name} plan` }),
      ).toBeVisible();
    }

    // A single column means every card shares the same left edge. Compare
    // rounded pixels so sub-pixel layout rounding does not flake.
    const lefts = await plans.evaluateAll((cards) =>
      cards.map((card) => Math.round(card.getBoundingClientRect().x)),
    );
    expect(new Set(lefts).size).toBe(1);

    await expectNoHorizontalOverflow(page);
  });
});
