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
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
