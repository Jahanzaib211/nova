/**
 * Live smoke test: open localhost:2026 with a real browser, capture
 * console + page errors, dump the rendered DOM region around the
 * "Something went wrong" fallback if it appears.
 */
import { chromium } from "@playwright/test";

async function main(): Promise<void> {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/home/jahanzaib/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
  });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  const errors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => errors.push(`[pageerror] ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(`[console.error] ${msg.text()}`);
  });

  for (const url of [
    "http://localhost:2026/",
    "http://localhost:2026/workspace",
    "http://localhost:2026/workspace/chats/689072e7-a0b4-4f1a-98c7-9d0e8ec82b96",
  ]) {
    console.log(`\n=== ${url} ===`);
    try {
      const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
      console.log(`status: ${resp?.status()}`);
      // Wait briefly for client hydration / errors.
      await page.waitForTimeout(1500);
      const body = await page.locator("body").innerText().catch(() => "(no body)");
      const headline = body.split("\n").slice(0, 12).join("\n  ");
      console.log(`dom (first 12 lines):\n  ${headline}`);
    } catch (err) {
      console.log(`navigation error: ${(err as Error).message}`);
    }
  }

  console.log("\n=== PAGE ERRORS ===");
  for (const e of errors) console.log(e);
  console.log("\n=== CONSOLE ERRORS ===");
  for (const e of consoleErrors) console.log(e);

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
