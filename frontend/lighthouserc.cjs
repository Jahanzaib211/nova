/**
 * Lighthouse CI configuration for the Nova frontend.
 *
 * Runs against a locally-built production server (`next build && next start`)
 * rather than the dev server: dev-mode bundles are uncompiled and would make
 * every performance number meaningless.
 *
 * Auth: the gated routes redirect to sign-in, so the collected URLs are the
 * public surface plus the chat shell with `DEER_FLOW_AUTH_DISABLED=1` — the
 * same switch `playwright.config.ts` already uses for e2e.
 *
 * Port: `pnpm start` defaults to 3000, which on this box is regularly taken by
 * an unrelated project — the same trap frontend/CLAUDE.md documents for
 * Playwright, where the tooling happily measures *that* app instead and the
 * numbers look fine while meaning nothing. LHCI_PORT overrides it, and the
 * collected URLs are built from the same value so the two cannot drift apart.
 *
 * Upload target is `filesystem`, so this needs no LHCI server and no network:
 * reports land in `.lighthouseci/` and the gate script folds them into
 * `~/.nova/gates/lighthouse.json` for the Nova Ops console.
 *
 * Budgets are deliberately set at the level the app currently clears, not at
 * an aspirational 100. A gate that is red on day one gets ignored; these are
 * ratchets meant to be raised as the numbers improve.
 */
const PORT = process.env.LHCI_PORT || "3210";

module.exports = {
  ci: {
    collect: {
      startServerCommand: `pnpm start --port ${PORT}`,
      startServerReadyPattern: "Ready in|started server on|Local:",
      startServerReadyTimeout: 120000,
      // Real routes only. `/chat` does not exist — the chat surface is under
      // /workspace/chats — and Lighthouse fails the whole run on a 404 rather
      // than skipping the URL.
      url: [
        `http://localhost:${PORT}/`,
        `http://localhost:${PORT}/workspace/chats`,
      ],
      numberOfRuns: 3,
      settings: {
        preset: "desktop",
        // Hosted runners: /dev/shm is 64 MB and there is no GPU. Without these
        // flags Chrome stalls mid-trace and the run dies with a DevTools
        // protocol timeout (`Network.getResponseBody`) that has nothing to do
        // with the page. Same fix docker/dev-entrypoint.sh applies to the
        // sandbox browser.
        chromeFlags: "--no-sandbox --disable-dev-shm-usage --disable-gpu",
        // Keep the profile between the 3 runs of a URL: a cold storage reset
        // re-triggers the first-load fetches that the timeout was hit on.
        disableStorageReset: true,
        // The sandbox/CDP surface and SSE streams keep sockets open, which
        // makes Lighthouse's network-idle heuristic wait forever.
        maxWaitForLoad: 45000,
        skipAudits: [
          "uses-http2",
          "canonical",
          "is-on-https",
          "redirects-http",
        ],
      },
    },
    assert: {
      // No `preset`. `lighthouse:recommended` asserts every individual audit at
      // >= 0.9, which is the opposite of the intent here: budgets are set where
      // the app currently sits so the gate is meaningful on day one, and get
      // ratcheted up deliberately. The preset also asserts that audits listed
      // in `skipAudits` above ran, so it fails on audits we chose not to run.
      //
      // Measured 2026-08-20 on a real production build: performance 59,
      // accessibility 91, best-practices 95, SEO 100. Each floor sits just
      // below its current value, so a regression trips it and normal variance
      // does not.
      assertions: {
        "categories:performance": ["warn", { minScore: 0.55 }],
        "categories:accessibility": ["error", { minScore: 0.88 }],
        "categories:best-practices": ["warn", { minScore: 0.9 }],
        "categories:seo": ["warn", { minScore: 0.95 }],
      },
    },
    upload: {
      target: "filesystem",
      outputDir: "./.lighthouseci",
      reportFilenamePattern: "%%PATHNAME%%-%%DATETIME%%-report.%%EXTENSION%%",
    },
  },
};
