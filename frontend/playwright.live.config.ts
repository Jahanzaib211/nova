import { defineConfig, devices } from "@playwright/test";

// Live config - drives the public deployment at https://nova.alilabsx.com
// using a freshly seeded test user. No webServer, no local stack.
//
// Run with:
//   cd frontend && \
//   E2E_LIVE_URL=https://nova.alilabsx.com \
//   E2E_USER_EMAIL=subagent-test@nova-alilabsx.com \
//   E2E_USER_PASSWORD=SubagentTest!2026 \
//   npx playwright test --config playwright.live.config.ts
export default defineConfig({
  testDir: "./tests/live",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: process.env.E2E_LIVE_URL ?? "https://nova.alilabsx.com",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
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
