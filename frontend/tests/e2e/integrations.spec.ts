import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Settings › Integrations against a mocked /api/integrations: grouped cards,
 * honest states (degraded with its reason, never-checked), probe-all and
 * per-card probe round-trips, and the page hidden while the flag is off.
 */

const NOW = "2026-09-19T12:00:00Z";
const items = [
  {
    id: "ollama",
    kind: "llm_gateway",
    display_name: "Ollama",
    endpoint: "http://host.docker.internal:11434",
    status: "healthy",
    latency_ms: 31,
    checked_at: NOW,
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
    checked_at: NOW,
    detail: "reachable, API key not accepted or MAILCOW_API_KEY not set",
    capabilities: [],
  },
  {
    id: "mcp:github",
    kind: "mcp_server",
    display_name: "github",
    endpoint: null,
    status: "healthy",
    latency_ms: null,
    checked_at: NOW,
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
];

function capabilities(features: Record<string, boolean>) {
  return {
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      features,
      skills: [],
      tools: [],
      hooks: [],
      subagents: [],
      circuits: [],
      server: {},
    }),
  };
}

test.describe("settings › integrations", () => {
  test("lists grouped cards with honest states and probes on demand", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/runtime/capabilities", (r) =>
      r.fulfill(capabilities({ integrations: true })),
    );
    let listCalls = 0;
    let refreshCalls = 0;
    await page.route("**/api/integrations", (r) => {
      if (new URL(r.request().url()).searchParams.get("refresh"))
        refreshCalls++;
      else listCalls++;
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          probe_cache_seconds: 20,
          integrations: items,
        }),
      });
    });
    await page.route("**/api/integrations?refresh=1", (r) => {
      refreshCalls++;
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          probe_cache_seconds: 20,
          integrations: items,
        }),
      });
    });
    await page.route("**/api/integrations/mailcow/probe", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...items[1],
          status: "healthy",
          detail: "v2026-08",
          capabilities: ["mailbox", "alias", "dkim"],
        }),
      }),
    );

    await page.goto("/workspace/settings#integrations");
    const cards = page.getByTestId("integration-card");
    await expect(cards).toHaveCount(4);
    await expect(page.getByTestId("integrations-summary")).toContainText(
      "2 healthy",
    );
    await expect(page.getByTestId("integrations-summary")).toContainText(
      "1 degraded",
    );
    // Group headings, in display order.
    const headings = page.locator("section header .text-lg");
    await expect(headings).toContainText([
      "Integrations",
      "Model gateways",
      "Mail, CRM & helpdesk",
      "Agent gateways",
      "MCP servers & skills",
    ]);
    // Honest states: the degraded card carries its reason; a never-probed
    // adapter says so instead of showing a timestamp.
    const mailcow = cards.filter({ hasText: "Mailcow" });
    await expect(mailcow).toHaveAttribute("data-status", "degraded");
    await expect(mailcow).toContainText("MAILCOW_API_KEY not set");
    await expect(cards.filter({ hasText: "claude_code" })).toContainText(
      "never checked",
    );
    // Adapters have no endpoint, so no Probe button.
    await expect(
      cards.filter({ hasText: "github" }).getByRole("button"),
    ).toHaveCount(0);

    await mailcow.getByRole("button", { name: "Probe Mailcow" }).click();
    await expect(mailcow).toHaveAttribute("data-status", "healthy");
    await expect(mailcow).toContainText("v2026-08");

    expect(refreshCalls).toBe(0);
    await page.getByRole("button", { name: "Probe all" }).click();
    await expect.poll(() => refreshCalls).toBe(1);
    expect(listCalls).toBeGreaterThanOrEqual(1);
  });

  test("is not offered while the server flag is off", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/runtime/capabilities", (r) =>
      r.fulfill(capabilities({ integrations: false })),
    );
    await page.goto("/workspace/settings#account");
    await expect(
      page.getByRole("button", { name: "Integrations" }),
    ).toHaveCount(0);
  });
});
