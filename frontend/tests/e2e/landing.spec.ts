import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Marketing landing page (`/`).
 *
 * These tests pin the investor-kit story: the production loop, the security
 * tools that sit inside it, the proof numbers and the pricing teaser. They also
 * guard the two things that are easy to lose in a redesign — the `en-US`
 * document locale and the CSS shimmer effects.
 */

test.describe("Landing page — content and navigation", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders the kit hero and trust signals", async ({ page }) => {
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /ai developer for companies that can't send their code to the cloud/i,
      }),
    ).toBeVisible();

    const trust = page.getByRole("list", { name: "Platform highlights" });
    await expect(
      page.getByText("Open core · self-hosted · MIT licensed"),
    ).toBeVisible();
    await expect(
      trust.getByText("Runs on your hardware", { exact: true }),
    ).toBeVisible();
    await expect(
      trust.getByText("40 security tools in every sandbox", { exact: true }),
    ).toBeVisible();
    await expect(
      trust.getByText("Any model, cloud or local", { exact: true }),
    ).toBeVisible();

    // The hero console is the visual proof of the production loop.
    await expect(page.getByText("nova / production loop")).toBeVisible();
    await expect(page.getByText("secure-ship gate: ready")).toBeVisible();
  });

  test("shows all five steps of the production loop", async ({ page }) => {
    const loop = page.locator("#loop");
    await expect(
      loop.getByRole("heading", {
        name: /one loop: ask, build, verify, secure, ship/i,
      }),
    ).toBeVisible();

    const steps = loop.locator("ol > li");
    await expect(steps).toHaveCount(5);
    for (const step of ["Ask", "Build", "Verify", "Secure", "Ship"]) {
      await expect(
        steps.filter({ has: page.getByRole("heading", { name: step }) }),
      ).toHaveCount(1);
    }

    await expect(
      loop.getByText(/security is not a scan somebody remembers/i),
    ).toBeVisible();
  });

  test("explains the six security layers", async ({ page }) => {
    const security = page.locator("#security");
    await expect(
      security.getByRole("heading", {
        name: /security at every layer of the agent stack, from day one/i,
      }),
    ).toBeVisible();

    // Six defence-in-depth layers from the kit.
    for (const layer of [
      "Model keys",
      "Tool access (MCP)",
      "Outside agents (ACP)",
      "Sandbox",
      "Platform",
      "Any model",
    ]) {
      await expect(
        security.getByRole("heading", { level: 3, name: layer }),
      ).toBeVisible();
    }
  });

  test("describes the live security tools and the Secure-Ship gate", async ({
    page,
  }) => {
    const security = page.locator("#secure-vibe-coding");
    await expect(
      security.getByRole("heading", { name: /code fast. ship safe./i }),
    ).toBeVisible();
    await expect(security.getByText("Secure vibe coding (beta)")).toBeVisible();
    await expect(
      security.getByRole("heading", { name: "Live today" }),
    ).toBeVisible();
    await expect(
      security.getByRole("heading", { name: "In beta, shipping next" }),
    ).toBeVisible();

    await expect(
      security.getByText(/40 security tools in every sandbox covering/i),
    ).toBeVisible();
    await expect(
      security.getByText(/flagged when the agent writes a file/i),
    ).toBeVisible();
    await expect(
      security.getByText(/high-risk shell commands are blocked/i),
    ).toBeVisible();
    await expect(
      security.getByText(/pre-deploy gate for secrets, vulnerable/i),
    ).toBeVisible();
    await expect(
      security.getByText(/critical or high findings block the handover/i),
    ).toBeVisible();
    await expect(security.getByText(/deep scans through trivy/i)).toBeVisible();
  });

  test("shows the measured proof numbers and first buyers", async ({
    page,
  }) => {
    const proof = page.locator("#proof");
    await expect(proof.getByText("8,658")).toBeVisible();
    await expect(proof.getByText("285")).toBeVisible();
    await expect(proof.getByText("Live", { exact: true })).toBeVisible();

    for (const buyer of [
      "Software agencies",
      "Regulated SMEs and mid-market",
      "Security consultancies and MSSPs",
      "Technical founders",
    ]) {
      await expect(
        proof.getByRole("heading", { level: 4, name: buyer }),
      ).toBeVisible();
    }
  });

  test("pricing teaser uses the kit prices and links to the pricing page", async ({
    page,
  }) => {
    const pricing = page.locator("#pricing");
    await expect(pricing.getByText("Free", { exact: true })).toBeVisible();
    await expect(pricing.getByText("$49", { exact: true })).toBeVisible();
    await expect(pricing.getByText("from $12,000")).toBeVisible();
    await expect(pricing.getByText("from $5,000")).toBeVisible();

    await pricing.getByRole("link", { name: /see full pricing/i }).click();
    await expect(page).toHaveURL(/\/saas$/);
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /ai developer for companies that can't send their code to the cloud/i,
      }),
    ).toBeVisible();
  });

  test("header and footer expose the kit navigation", async ({ page }) => {
    const header = page.locator("header").first();
    await expect(header.getByRole("link", { name: "Product" })).toHaveAttribute(
      "href",
      "/#loop",
    );
    await expect(
      header.getByRole("link", { name: "Security" }),
    ).toHaveAttribute("href", "/#security");
    await expect(header.getByRole("link", { name: "Pricing" })).toHaveAttribute(
      "href",
      "/saas",
    );
    await expect(header.getByRole("link", { name: "Docs" })).toHaveAttribute(
      "href",
      "/en/docs",
    );

    const footer = page.locator("footer");
    await expect(
      footer.getByRole("link", { name: "Jahanzaib Ali" }),
    ).toHaveAttribute("href", "https://alilabsx.com/");
  });

  test("uses the en-US document locale", async ({ page }) => {
    await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
  });

  test("does not leak the old product copy", async ({ page }) => {
    const body = await page.locator("body").innerText();
    for (const stale of [
      "SuperAgent",
      "Super Agent",
      "ByteWorld",
      "BytePlus",
      "DeerFlow",
      "Self-bootstrapped",
      "$29",
      "Get Started with 2.0",
    ]) {
      expect(body, `stale copy: ${stale}`).not.toContain(stale);
    }
  });

  test("Start building opens the workspace", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.getByRole("link", { name: "Start building" }).first().click();

    await page.waitForURL(/\/workspace/, { timeout: 15_000 });
    await expect(page).toHaveURL(/\/workspace/);
  });
});

test.describe("Landing page — shimmer effects", () => {
  test("animates text highlights and card sweeps", async ({ page }) => {
    await page.goto("/");

    const text = page.locator('[data-shimmer="text"]').first();
    await expect(text).toBeVisible();
    const textAnimation = await text.evaluate(
      (el) => getComputedStyle(el).animationName,
    );
    expect(textAnimation).toContain("nova-shimmer");

    const sweep = page.locator('[data-shimmer="sweep"]').first();
    await expect(sweep).toBeVisible();
    const sweepAnimation = await sweep.evaluate(
      (el) => getComputedStyle(el, "::after").animationName,
    );
    expect(sweepAnimation).toContain("nova-shimmer-sweep");
  });

  test("is turned off under prefers-reduced-motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");

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
