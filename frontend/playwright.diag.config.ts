import { defineConfig, devices } from "@playwright/test";

// Diagnostic-only config — separate from the production playwright.config.ts
// because that file pins testDir to ./tests/e2e (which excludes this folder).
// Run with:  E2E_PORT=3000 npx playwright test --config playwright.diag.config.ts
const PORT = process.env.E2E_PORT ?? "3000";

export default defineConfig({
  testDir: "./tests/_diag",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 180_000,
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Playwright publishes no chromium build for Ubuntu 26.04, and the
        // pinned revision may not match whatever is in ~/.cache/ms-playwright
        // if another project installed a different Playwright version. Allow an
        // explicit binary so diagnostics stay runnable on this host:
        //   PLAYWRIGHT_CHROME_PATH=~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome
        ...(process.env.PLAYWRIGHT_CHROME_PATH
          ? {
              launchOptions: {
                executablePath: process.env.PLAYWRIGHT_CHROME_PATH,
              },
            }
          : {}),
      },
    },
  ],
});
