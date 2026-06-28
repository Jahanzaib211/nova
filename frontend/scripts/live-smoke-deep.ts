/**
 * Live smoke v2: deeper SSR test that follows workspace redirects and
 * exercises the runtime capabilities endpoint.
 */
import { chromium } from "@playwright/test";

async function main(): Promise<void> {
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      "/home/jahanzaib/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
  });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  const errors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => errors.push(`[pageerror] ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error")
      consoleErrors.push(`[console.error] ${msg.text()}`);
  });

  const tests = [
    { url: "http://localhost:2026/", name: "landing" },
    { url: "http://localhost:2026/workspace", name: "workspace" },
    { url: "http://localhost:2026/workspace/chats/new", name: "new chat" },
    {
      url: "http://localhost:2026/api/runtime/capabilities",
      name: "capabilities API",
    },
    { url: "http://localhost:2026/health", name: "health" },
  ];

  for (const t of tests) {
    console.log(`\n=== ${t.name} (${t.url}) ===`);
    try {
      const resp = await page.goto(t.url, {
        waitUntil: "domcontentloaded",
        timeout: 20000,
      });
      console.log(`status: ${resp?.status()}`);
      await page.waitForTimeout(1000);
      const body = await page
        .locator("body")
        .innerText()
        .catch(() => "(no body)");
      const headline = body.split("\n").slice(0, 8).join("\n  ");
      console.log(`dom (first 8 lines):\n  ${headline}`);
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
