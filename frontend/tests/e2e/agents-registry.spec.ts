import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/** The agent registry on /workspace/agents and the Jobs page, with live counts. */
const registry = {
  async_enabled: true,
  agents: [
    {
      id: "lead",
      kind: "lead",
      name: "Nova",
      description: "Lead.",
      model: "m",
      runner: "gateway",
      async_capable: false,
      queued: 0,
      running: 0,
    },
    {
      id: "subagent:general-purpose",
      kind: "subagent",
      name: "general-purpose",
      description: "gp",
      model: "inherit",
      runner: "jobs",
      async_capable: true,
      queued: 2,
      running: 1,
    },
    {
      id: "acp:claude_code",
      kind: "acp",
      name: "claude_code",
      description: "cc",
      model: null,
      runner: "jobs",
      async_capable: true,
      queued: 0,
      running: 0,
    },
  ],
};

test("agents page and jobs page show the registry with live counts", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/runtime/capabilities", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        features: { jobs: true },
        skills: [],
        tools: [],
        hooks: [],
        subagents: [],
        circuits: [],
        server: {},
      }),
    }),
  );
  await page.route("**/api/agents/registry", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(registry),
    }),
  );
  await page.route("**/api/jobs?**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ jobs: [], limit: 100, offset: 0 }),
    }),
  );
  await page.route("**/api/jobs/schedules", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ schedules: [] }),
    }),
  );

  await page.goto("/workspace/agents");
  const list = page.getByTestId("agents-registry");
  await expect(list).toHaveAttribute("data-async", "true");
  await expect(list.getByTestId("registry-agent")).toHaveCount(3);
  await expect(list.getByTestId("registry-counts")).toHaveText(
    "1 running · 2 queued",
  );
  await expect(list).toContainText("1 running, 2 queued");

  await page.goto("/workspace/jobs");
  await expect(
    page.getByTestId("jobs-agents").getByTestId("registry-agent"),
  ).toHaveCount(3);
});
