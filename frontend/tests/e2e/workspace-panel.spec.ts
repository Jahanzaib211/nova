/**
 * E2E spec: workspace-aware Agent Computer panel tabs (C10).
 *
 * Drives the real Files / Telemetry / Review tabs against a mocked
 * /api/workspace/* surface (mockWorkspaceAPI in ./utils/mock-api) instead
 * of a live backend or the intelligence_enabled flag, closing the gap left
 * by agent-computer.spec.ts (chrome-only: opens the panel, asserts a tab
 * exists) and the jsdom-based unit smoke test (renders to static markup,
 * never exercises real navigation/data-fetching in a browser).
 */

import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, mockWorkspaceAPI } from "./utils/mock-api";

test.describe("Workspace-aware panel tabs", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);
    mockWorkspaceAPI(page);
  });

  async function openPanel(page: Page) {
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(
        true,
        "agent computer trigger not visible (sign-in gate or auth-disabled off)",
      );
      return false;
    }
    await trigger.first().click();
    return true;
  }

  test("Files tab renders the workspace card from a mocked snapshot", async ({
    page,
  }) => {
    if (!(await openPanel(page))) return;

    await page.getByRole("tab", { name: "Files", exact: true }).click();
    // Scoped to the Files tab's own DOM subtree (`data-tab="files"`): tabs
    // stay mounted-but-hidden across switches (agent-computer-panel.tsx),
    // so an unscoped page-wide text match is ambiguous — the Telemetry tab
    // renders the same mocked snapshot's symbol count in its own banner.
    const filesTab = page.locator('[data-tab="files"]');
    // WorkspaceCard renders project_count/symbol_count/command_count from
    // the mocked /snapshot response — this is real fetch + real render,
    // not a synthetic prop.
    await expect(filesTab.getByText("2", { exact: true }).first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(filesTab.getByText(/1,234|1234/)).toBeVisible();
  });

  test("Telemetry tab shows the live-indexed banner and a live scan event", async ({
    page,
  }) => {
    if (!(await openPanel(page))) return;

    await page.getByRole("tab", { name: "Telemetry", exact: true }).click();
    // Static banner from useWorkspaceSnapshot (mocked /snapshot).
    await expect(page.getByText(/workspace indexed/i)).toBeVisible({
      timeout: 10_000,
    });
    // Live strip from useWorkspaceEvents (mocked /events SSE frame).
    await expect(page.getByText(/live scan/i)).toBeVisible({ timeout: 10_000 });
  });

  test("Review tab surfaces no kernel verdict before any plan is built", async ({
    page,
  }) => {
    if (!(await openPanel(page))) return;

    await page.getByRole("tab", { name: "Review", exact: true }).click();
    // The mocked /events stream only emits WorkspaceScanned, never
    // PlanBuilt — the kernel-verdict card must stay absent, not error.
    await expect(page.getByText(/workspace kernel verdict/i)).toHaveCount(0);
  });
});
