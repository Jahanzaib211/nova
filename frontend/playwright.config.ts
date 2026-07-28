import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.E2E_PORT ?? "3000";

// Shared with the mobile project below so a custom local browser build applies
// to both.
const chromiumLaunchOverride = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  ? {
      launchOptions: {
        executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      },
    }
  : {};

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
      // The *.mobile.spec.ts files assert phone-only layouts and belong to the
      // mobile project below.
      testIgnore: /.*\.mobile\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        // v7.3 (Nova rebrand): removed the hardcoded executablePath that
        // pinned to chromium-1228 on one developer's box. CI runners
        // install a different chromium build (via `npx playwright install
        // chromium --with-deps`), so the pinned path 404'd every CI run.
        // Now we use the version Playwright installs by default.
        // Local developers with a custom browser path can still override
        // via PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH env var.
        ...chromiumLaunchOverride,
      },
    },
    {
      // Guards the responsive work: the workspace collapses its side-by-side
      // panels into a switcher and the landing nav moves behind a sheet below
      // the 768px breakpoint, neither of which Desktop Chrome exercises.
      name: "mobile-chrome",
      testMatch: /.*\.mobile\.spec\.ts/,
      use: {
        ...devices["Pixel 7"],
        ...chromiumLaunchOverride,
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
