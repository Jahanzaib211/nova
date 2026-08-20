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
 * Upload target is `filesystem`, so this needs no LHCI server and no network:
 * reports land in `.lighthouseci/` and the gate script folds them into
 * `~/.nova/gates/lighthouse.json` for the Nova Ops console.
 *
 * Budgets are deliberately set at the level the app currently clears, not at
 * an aspirational 100. A gate that is red on day one gets ignored; these are
 * ratchets meant to be raised as the numbers improve.
 */
module.exports = {
  ci: {
    collect: {
      startServerCommand: "pnpm start",
      startServerReadyPattern: "Ready in|started server on|Local:",
      startServerReadyTimeout: 120000,
      url: ["http://localhost:3000/", "http://localhost:3000/chat"],
      numberOfRuns: 3,
      settings: {
        preset: "desktop",
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
      preset: "lighthouse:recommended",
      assertions: {
        "categories:performance": ["warn", { minScore: 0.5 }],
        "categories:accessibility": ["error", { minScore: 0.85 }],
        "categories:best-practices": ["warn", { minScore: 0.8 }],
        "categories:seo": ["warn", { minScore: 0.8 }],

        // Informational in a local run; they measure the network, not the app.
        "server-response-time": "off",
        "uses-long-cache-ttl": "off",
        "unused-javascript": "off",
        "unused-css-rules": "off",
        "total-byte-weight": "off",
        "legacy-javascript": "off",
        "unsized-images": "warn",
        "csp-xss": "warn",
      },
    },
    upload: {
      target: "filesystem",
      outputDir: "./.lighthouseci",
      reportFilenamePattern: "%%PATHNAME%%-%%DATETIME%%-report.%%EXTENSION%%",
    },
  },
};
