/**
 * Visual-regression baseline (2026-09-18 upgrade program, phase 0).
 *
 * One shared body, two entry files (`visual.spec.ts` for the chromium project,
 * `visual.mobile.spec.ts` for mobile-chrome) so every screen is pinned at both
 * form factors. Snapshots live under `tests/e2e/visual/__snapshots__/<project>/`
 * (see `snapshotPathTemplate` in playwright.config.ts).
 *
 * Opt-in: font rendering differs between machines, so the gate only runs when
 * `NOVA_VISUAL=1` (CI runs it inside the Playwright container). Update
 * snapshots only in dedicated commits: `vr: update snapshots (<reason>)`.
 *
 *   NOVA_VISUAL=1 E2E_PORT=3111 CI=1 pnpm exec playwright test tests/e2e/visual --update-snapshots
 */
import { expect, test, type Page } from "@playwright/test";

import {
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  mockSandboxAPI,
  sandboxFile,
} from "../utils/mock-api";

const SETTINGS_SECTIONS = [
  "account",
  "appearance",
  "memory",
  "runtime",
  "tools",
  "skills",
  "notification",
  "voice",
  "jobs",
  "integrations",
  "email",
  "channels",
  "models",
  "about",
] as const;

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

/** Deterministic backend: every API the settings/workspace surfaces touch. */
async function mockEverything(page: Page) {
  // Registered first so the specific mocks below take precedence; anything
  // unmocked gets a fast 404 instead of a hanging socket.
  await page.route("**/api/**", (route) =>
    route.fulfill(json({ detail: "mocked-404" }, 404)),
  );
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Visual baseline thread",
        updated_at: "2026-09-18T12:00:00Z",
        artifacts: ["/mnt/user-data/outputs/report.md"],
      },
    ],
  });
  mockSandboxAPI(page, {
    files: [
      sandboxFile("/mnt/user-data/outputs/report.md", 222),
      sandboxFile("/mnt/user-data/workspace/src/index.ts", 555),
    ],
  });
  await page.route("**/api/voice/status", (r) =>
    r.fulfill(json({ enabled: true, ready: true, sample_rate: 16000 })),
  );
  await page.route("**/api/voice/config", (r) =>
    r.fulfill(json({ enabled: true, stt: {}, tts: {} })),
  );
  await page.route("**/api/mcp/config", (r) =>
    r.fulfill(json({ mcpServers: {} })),
  );
  await page.route("**/api/runtime/config", (r) =>
    r.fulfill(
      json({
        summarization: { enabled: true },
        subagents: { enabled: true, max_concurrent: 3 },
        guardrails: { enabled: true },
      }),
    ),
  );
  await page.route("**/api/jobs?**", (r) =>
    r.fulfill(json({ jobs: [], limit: 100, offset: 0 })),
  );
  await page.route("**/api/jobs/schedules", (r) =>
    r.fulfill(json({ schedules: [] })),
  );
  await page.route("**/api/integrations**", (r) =>
    r.fulfill(
      json({
        enabled: true,
        probe_cache_seconds: 20,
        integrations: [
          {
            id: "ollama",
            kind: "llm_gateway",
            display_name: "Ollama",
            endpoint: "http://host.docker.internal:11434",
            status: "healthy",
            latency_ms: 31,
            checked_at: "2026-09-19T12:00:00Z",
            detail: "4 models",
            capabilities: ["qwen3:8b", "nomic-embed"],
          },
          {
            id: "mailcow",
            kind: "mail",
            display_name: "Mailcow",
            endpoint: "http://host.docker.internal:8080",
            status: "degraded",
            latency_ms: 40,
            checked_at: "2026-09-19T12:00:00Z",
            detail: "reachable, MAILCOW_API_KEY not set",
            capabilities: [],
          },
          {
            id: "mcp:github",
            kind: "mcp_server",
            display_name: "github",
            endpoint: null,
            status: "healthy",
            latency_ms: null,
            checked_at: "2026-09-19T12:00:00Z",
            detail: "12 tools",
            capabilities: ["stdio"],
          },
          {
            id: "acp:claude_code",
            kind: "acp_agent",
            display_name: "claude_code",
            endpoint: null,
            status: "down",
            latency_ms: null,
            checked_at: null,
            detail: "npx not found on PATH",
            capabilities: ["acp"],
          },
        ],
      }),
    ),
  );
  await page.route("**/api/em/lists", (r) =>
    r.fulfill(
      json({
        lists: [
          {
            id: "l1",
            name: "Newsletter",
            description: null,
            member_count: 42,
            created_at: "2026-09-19T12:00:00Z",
            updated_at: "2026-09-19T12:00:00Z",
          },
        ],
      }),
    ),
  );
  await page.route("**/api/em/templates", (r) =>
    r.fulfill(json({ templates: [] })),
  );
  await page.route("**/api/em/contacts?**", (r) =>
    r.fulfill(json({ contacts: [], total: 0 })),
  );
  await page.route("**/api/em/suppressions", (r) =>
    r.fulfill(json({ suppressions: [] })),
  );
  await page.route("**/api/em/campaigns", (r) =>
    r.fulfill(
      json({
        campaigns: [
          {
            id: "c1",
            name: "September update",
            list_id: "l1",
            template_id: "t1",
            from_email: "hello@example.com",
            from_name: "Nova",
            reply_to: null,
            status: "sending",
            scheduled_at: null,
            throttle_per_minute: null,
            stats: {
              recipients: 42,
              queued: 10,
              sent: 32,
              opened: 12,
              clicked: 3,
            },
            job_id: "j1",
            error: null,
            created_at: "2026-09-19T12:00:00Z",
            started_at: "2026-09-19T12:00:00Z",
            finished_at: null,
          },
          {
            id: "c2",
            name: "Welcome series",
            list_id: "l1",
            template_id: "t1",
            from_email: "hello@example.com",
            from_name: "Nova",
            reply_to: null,
            status: "draft",
            scheduled_at: null,
            throttle_per_minute: null,
            stats: {},
            job_id: null,
            error: null,
            created_at: "2026-09-19T12:00:00Z",
            started_at: null,
            finished_at: null,
          },
        ],
      }),
    ),
  );
  await page.route("**/api/em/bridges/status", (r) =>
    r.fulfill(
      json({
        bridges: {
          mailcow: {
            enabled: true,
            configured: false,
            problems: ["MAILCOW_API_KEY is not set"],
            endpoint: "http://host.docker.internal:8080",
          },
          twenty: {
            enabled: true,
            configured: false,
            problems: ["TWENTY_API_KEY is not set"],
            endpoint: "http://host.docker.internal:3008",
          },
          chatwoot: {
            enabled: false,
            configured: false,
            problems: ["email_marketing.bridges.chatwoot is false"],
            endpoint: null,
          },
        },
      }),
    ),
  );
  await page.route("**/api/runtime/capabilities", (r) =>
    r.fulfill(
      json({
        features: { jobs: true, integrations: true, email_marketing: true },
        skills: [],
        tools: [],
        hooks: [],
        subagents: [],
        circuits: [],
        server: {},
      }),
    ),
  );
  await page.route("**/api/memory**", (r) =>
    r.fulfill(json({ enabled: false, memories: [] })),
  );
  await page.route("**/api/v1/credits**", (r) =>
    r.fulfill(json({ balance: 0, plan: "free" })),
  );
  await page.route("**/api/v1/billing**", (r) =>
    r.fulfill(json({ enabled: false })),
  );
  await page.route("**/api/v1/byok**", (r) => r.fulfill(json({ keys: [] })));
  await page.route("**/api/v1/referral**", (r) =>
    r.fulfill(json({ code: "VISUAL", referrals: 0 })),
  );
}

// Decorative canvases (Galaxy WebGL starfield, FlickeringGrid) repaint every
// frame from random seeds, so they can never match a snapshot; hide them.
// A frozen clock is not an option: toHaveScreenshot itself needs the page's
// requestAnimationFrame and deadlocks under page.clock.pauseAt().
// The hero headline is masked, but its box width follows the rotating word;
// pin it to the full row so the mask edges never move.
const FREEZE_CSS =
  "canvas { display: none !important; } h1:has(> .overflow-hidden.py-2) { width: 100% !important; }" +
  // Side panels animate their width; a frame caught mid-transition shifts
  // the whole Agent's Computer column by a few pixels (flaked once in 40).
  " [data-side-panel], [data-side-panel] > div { transition: none !important; }";

/** Wait until every side panel has held the same width for two samples. */
async function settlePanels(page: Page) {
  await page.waitForFunction(
    () =>
      new Promise<boolean>((resolve) => {
        const measure = () =>
          Array.from(document.querySelectorAll("[data-side-panel]"))
            .map((el) => Math.round(el.getBoundingClientRect().width))
            .join(",");
        const before = measure();
        setTimeout(() => resolve(measure() === before), 200);
      }),
    undefined,
    { timeout: 5_000 },
  );
}

// Rotating hero word (WordRotate) and anything time-based.
const MASK_SELECTORS = [
  "time",
  "[data-vr-mask]",
  "[data-testid='token-usage']",
  // The whole hero headline: the rotating word changes width, which reflows
  // the "with Nova" beside it, so masking the word alone was not enough.
  "h1:has(> .overflow-hidden.py-2)",
];

async function snap(page: Page, name: string) {
  await page.addStyleTag({ content: FREEZE_CSS });
  // Let entrance transitions (motion/react) settle.
  await page.waitForTimeout(1_500);
  await settlePanels(page);
  // A capture taken while a web font is still swapping in differs from the
  // baseline in every glyph's anti-aliasing (seen as a ~0.3 % "flake").
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await expect(page).toHaveScreenshot(`${name}.png`, {
    animations: "disabled",
    caret: "hide",
    // 0.1 % of a 1280x720 frame is ~900 px: enough to absorb antialiasing,
    // small enough that a changed line of text fails. 1 % let a whole verdict
    // line change pass unnoticed.
    maxDiffPixelRatio: 0.001,
    timeout: 15_000,
    mask: MASK_SELECTORS.map((s) => page.locator(s)),
  });
}

export function defineVisualTests() {
  test.skip(
    !process.env.NOVA_VISUAL,
    "set NOVA_VISUAL=1 to run the visual-regression gate",
  );
  // Independent screens: a single capture hiccup must not skip the rest, and
  // one retry absorbs the occasional "Unable to capture screenshot".
  test.describe.configure({ retries: 1 });
  // Full Chromium (new headless) instead of the headless shell, with software
  // GL: pixel-deterministic across machines, and the landing's WebGL starfield
  // otherwise wedges the shell's compositor so the *next* page's
  // captureScreenshot fails with "Unable to capture screenshot".
  test.use({
    channel: "chromium",
    launchOptions: {
      args: ["--use-gl=angle", "--use-angle=swiftshader"],
    },
  });

  test.beforeEach(async ({ page }) => {
    // Viewport-driven autoplay (the landing skills demo) and reveal-on-scroll
    // effects keep repainting; a screenshot can never stabilise while they
    // run. Stub the observer so nothing "enters the viewport".
    await page.addInitScript(() => {
      class InertObserver {
        observe() {
          /* never intersects */
        }
        unobserve() {
          /* nothing observed */
        }
        disconnect() {
          /* nothing to disconnect */
        }
        takeRecords() {
          return [];
        }
      }
      Object.defineProperty(window, "IntersectionObserver", {
        value: InertObserver,
        writable: true,
      });
    });
    await mockEverything(page);
  });

  test("landing", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await snap(page, "landing");
  });

  // No "login" screen: the e2e server runs with auth disabled, so the (auth)
  // layout redirects /login to /workspace on the server. The snapshot that
  // used to live here was a picture of the workspace, and it flaked whenever
  // the follow-on client redirect (/workspace -> /workspace/chats/new) landed
  // mid-capture. The form has no e2e coverage yet: it needs a signed-out server.

  test("workspace empty chat", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    await page.waitForLoadState("networkidle");
    await snap(page, "workspace-empty");
  });

  test("workspace thread with artifacts", async ({ page }) => {
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.waitForLoadState("networkidle");
    await snap(page, "workspace-thread");
    const artifacts = page.getByRole("button", { name: /artifacts/i }).first();
    if (await artifacts.isVisible().catch(() => false)) {
      await artifacts.click();
      await snap(page, "workspace-artifacts-open");
    }
  });

  const COMPUTER_TABS = [
    "files",
    "terminal",
    "viewer",
    "browser",
    "review",
    "telemetry",
    "privacy",
  ] as const;

  test("agent's computer, every tab", async ({ page }) => {
    // Seven captures in one test (1.5 s settle each plus the compare); the
    // default 30 s budget was hit on a loaded box and reported as a flake.
    test.setTimeout(120_000);
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.waitForLoadState("networkidle");
    const trigger = page
      .getByRole("button", { name: /agent's computer/i })
      .first();
    // A click that lands before hydration attaches the handler does nothing
    // (seen on mobile-chrome under load); re-click until the tabs appear.
    await expect(async () => {
      await trigger.click();
      await expect(page.getByRole("tab").first()).toBeVisible({
        timeout: 2_000,
      });
    }).toPass({ timeout: 20_000 });
    for (const tab of COMPUTER_TABS) {
      const panel = page.locator(`[data-tab="${tab}"]`);
      // Tab labels follow t.agentComputer.tabs; "privacy" is labelled Recon.
      const label = tab === "privacy" ? "recon" : tab;
      await page
        .getByRole("tab", { name: new RegExp(`^${label}`, "i") })
        .first()
        .click();
      await expect(panel).toBeVisible();
      await snap(page, `computer-${tab}`);
    }
  });

  test("agents gallery", async ({ page }) => {
    await page.goto("/workspace/agents");
    await page.waitForLoadState("networkidle");
    await snap(page, "agents");
  });

  test("email page", async ({ page }) => {
    await page.goto("/workspace/email");
    await page.waitForLoadState("networkidle");
    await snap(page, "email");
  });

  test("jobs page", async ({ page }) => {
    await page.goto("/workspace/jobs");
    await page.waitForLoadState("networkidle");
    await snap(page, "jobs");
  });

  test("create agent", async ({ page }) => {
    await page.goto("/workspace/agents/new");
    await page.waitForLoadState("networkidle");
    await snap(page, "agents-new");
  });

  for (const section of SETTINGS_SECTIONS) {
    test(`settings ${section}`, async ({ page }) => {
      await page.goto(`/workspace/settings#${section}`);
      await page.waitForLoadState("networkidle");
      await snap(page, `settings-${section}`);
    });
  }
}
