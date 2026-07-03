import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.E2E_PORT ?? "3000";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "html",
  timeout: 30_000,

  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // v7.3 (Nova rebrand): removed the hardcoded executablePath that
        // pinned to chromium-1228 on one developer's box. CI runners
        // install a different chromium build (via `npx playwright install
        // chromium --with-deps`), so the pinned path 404'd every CI run.
        // Now we use the version Playwright installs by default.
        // Local developers with a custom browser path can still override
        // via PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH env var.
        ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? {
              launchOptions: {
                executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
              },
            }
          : {}),
      },
    },
  ],

  webServer: {
    command: `pnpm build && pnpm start --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      SKIP_ENV_VALIDATION: "1",
      DEER_FLOW_AUTH_DISABLED: "1",
    },
  },
});
