import { defineConfig, devices } from "@playwright/test";

// Overridable so the suite can avoid a port already owned by an unrelated
// local project. `reuseExistingServer` is on outside CI, so a collision on the
// default silently runs the whole suite against *someone else's app* — which
// looks exactly like a product regression. Same guard as playwright.config.ts.
const APP_PORT = process.env.E2E_PORT ?? "3000";
const GATEWAY_PORT = process.env.E2E_GATEWAY_PORT ?? "8011";

/**
 * Layer 2 of the record/replay e2e: the REAL Next.js frontend rendering data
 * from a REAL gateway whose LLM is the deterministic `ReplayChatModel` (no API
 * key). This is separate from `playwright.config.ts` (which mocks the backend)
 * so the mock-based suite is untouched.
 *
 * Two webServers are started: the replay gateway (:8011) and the frontend
 * (:3000, pointed at the gateway). Auth-disabled mode is enabled on both
 * servers so the no-cookie e2e contract is covered; specs that need session
 * cookies still register a throwaway test account at runtime.
 */
export default defineConfig({
  testDir: "./tests/e2e-real-backend",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "html",
  timeout: 90_000,

  use: {
    baseURL: `http://localhost:${APP_PORT}`,
    trace: "on-first-retry",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      command: `uv run python scripts/run_replay_gateway.py --port ${GATEWAY_PORT}`,
      cwd: "../backend",
      url: `http://localhost:${GATEWAY_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      // Mount the test-only run/message seeder used by multi-run-order.spec.ts
      // (#3352). The endpoint exists only on this replay gateway, never in the
      // production app.
      env: {
        DEERFLOW_ENABLE_TEST_SEED: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
        // `is_auth_disabled()` deliberately refuses to honour
        // DEER_FLOW_AUTH_DISABLED when the environment declares production —
        // a good guard. But the gateway calls load_dotenv() at import, so an
        // operator .env carrying DEER_FLOW_ENV=production (this box has one)
        // silently re-enabled auth and made this suite 401 as though the app
        // had regressed. Declare the harness's own environment explicitly.
        DEER_FLOW_ENV: "test",
      },
    },
    {
      command: `pnpm build && pnpm start --port ${APP_PORT}`,
      url: `http://localhost:${APP_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 240_000,
      env: {
        SKIP_ENV_VALIDATION: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
        BETTER_AUTH_SECRET: "local-dev-secret",
        // Leave NEXT_PUBLIC_* unset so the frontend uses its built-in
        // next.config rewrites (same-origin proxy) instead of talking to the
        // gateway cross-origin — cross-origin fetches drop the auth cookies.
        // Just point that proxy at the replay gateway.
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: `http://127.0.0.1:${GATEWAY_PORT}`,
      },
    },
  ],
});
