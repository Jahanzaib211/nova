import { expect, test } from "@playwright/test";

/**
 * Pricing page (`/saas`).
 *
 * Every number here comes from the investor kit. If a price, tier or the
 * open-core positioning changes, this spec should fail loudly.
 */

const PLANS = [
  { name: "Community", price: "Free", period: "forever" },
  { name: "Plus", price: "$49", period: "per seat / month" },
  { name: "Enterprise", price: "from $12,000", period: "per year" },
  { name: "Services", price: "from $5,000", period: "one-off" },
];

test.describe("Pricing page — plans and positioning", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/saas");
  });

  test("renders all four kit plans with exact prices", async ({ page }) => {
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /ai developer for companies that can't send their code to the cloud/i,
      }),
    ).toBeVisible();

    for (const plan of PLANS) {
      const card = page.getByRole("article", { name: `${plan.name} plan` });
      await expect(card).toBeVisible();
      await expect(
        card.getByRole("heading", { level: 2, name: plan.name }),
      ).toBeVisible();
      await expect(card.getByText(plan.price, { exact: true })).toBeVisible();
      await expect(card.getByText(plan.period)).toBeVisible();
    }
  });

  test("Plus is highlighted and carries Secure-Ship", async ({ page }) => {
    const plus = page.getByRole("article", { name: "Plus plan" });
    // The "Most popular" badge is a sibling of the card, inside the plan
    // wrapper, so assert against the wrapper rather than the card itself.
    const plusPlan = plus.locator("..");
    await expect(plusPlan.getByText("Most popular")).toBeVisible();
    await expect(
      plus.getByText("Secure-Ship (beta): pre-deploy security gate"),
    ).toBeVisible();
    await expect(
      plus.getByRole("link", { name: "Start a pilot" }),
    ).toHaveAttribute("href", /mailto:alilabsx@gmail\.com/);
  });

  test("plan CTAs point at the right destinations", async ({ page }) => {
    await expect(
      page
        .getByRole("article", { name: "Community plan" })
        .getByRole("link", { name: "Get the code" }),
    ).toHaveAttribute("href", "https://github.com/Jahanzaib211/nova");
    await expect(
      page
        .getByRole("article", { name: "Enterprise plan" })
        .getByRole("link", { name: "Talk to us" }),
    ).toHaveAttribute("href", /mailto:alilabsx@gmail\.com/);
    await expect(
      page
        .getByRole("article", { name: "Services plan" })
        .getByRole("link", { name: "Book deployment" }),
    ).toHaveAttribute("href", /mailto:alilabsx@gmail\.com/);
  });

  test("shows the measured proof points", async ({ page }) => {
    for (const value of ["40", "8,656", "285", "Any model"]) {
      await expect(page.getByText(value, { exact: true })).toBeVisible();
    }
    await expect(
      page.getByText("verified security tools in every sandbox", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText("API operations, with 40 tools over MCP", { exact: true }),
    ).toBeVisible();
  });

  test("explains the open-core, partner-led model", async ({ page }) => {
    await expect(
      page.getByRole("heading", {
        level: 2,
        name: "Open core, partner-led",
      }),
    ).toBeVisible();
    await expect(
      page.getByText(/Enterprise installs are sold, deployed and supported/i),
    ).toBeVisible();
    await expect(
      page.getByText(/30-day on-premise pilots convert to annual licences/i),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Ali Technologies" }),
    ).toHaveAttribute("href", "https://www.alilabsx.com");
  });

  test("uses the en-US document locale", async ({ page }) => {
    await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
  });

  test("does not contain legacy copy or the old violet gradient", async ({
    page,
  }) => {
    const body = await page.locator("body").innerText();
    for (const stale of [
      "$29",
      "Self-bootstrapped",
      "SuperAgent",
      "Super Agent",
      "ByteWorld",
      "BytePlus",
      "DeerFlow",
    ]) {
      expect(body, `stale copy: ${stale}`).not.toContain(stale);
    }

    const html = await page.content();
    expect(html).not.toContain("from-violet-600");
    expect(html).not.toContain("to-cyan-600");
  });
});

test.describe("Pricing page — shimmer effects", () => {
  test("animates the headline, proof numbers and highlighted card", async ({
    page,
  }) => {
    await page.goto("/saas");

    const text = page.locator('[data-shimmer="text"]').first();
    await expect(text).toBeVisible();
    await expect
      .poll(() => text.evaluate((el) => getComputedStyle(el).animationName))
      .toContain("nova-shimmer");

    const sweep = page.locator('[data-shimmer="sweep"]').first();
    await expect(sweep).toBeVisible();
    await expect
      .poll(() =>
        sweep.evaluate((el) => getComputedStyle(el, "::after").animationName),
      )
      .toContain("nova-shimmer-sweep");
  });

  test("is turned off under prefers-reduced-motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/saas");

    const text = page.locator('[data-shimmer="text"]').first();
    await expect(text).toBeVisible();
    await expect
      .poll(() => text.evaluate((el) => getComputedStyle(el).animationName))
      .toBe("none");

    const sweep = page.locator('[data-shimmer="sweep"]').first();
    await expect
      .poll(() =>
        sweep.evaluate((el) => getComputedStyle(el, "::after").animationName),
      )
      .toBe("none");
  });
});
