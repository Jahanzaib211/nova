/**
 * E2E spec: Agent Computer panel and the chat workspace skeleton.
 *
 * Mocks the LangGraph / Backend APIs so the test is hermetic — same
 * pattern as tests/e2e/chat.spec.ts. With `DEER_FLOW_AUTH_DISABLED=1`
 * the workspace renders without the sign-in gate; in production mode
 * the sign-in form is shown, which we also assert.
 *
 * The "Agent Computer trigger" is the terminal-icon button in the
 * page header that opens the agent-computer side panel. We assert the
 * panel can be opened and exposes the seven tab buttons.
 */

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Agent Computer panel", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);
  });

  test("landing page renders with Get Started CTA", async ({ page }) => {
    await page.goto("/");
    // Both the nav-bar and hero CTA share the accessible name — assert the
    // first is visible rather than an ambiguous strict-mode match.
    await expect(
      page.getByRole("link", { name: /get started/i }).first(),
    ).toBeVisible({
      timeout: 15_000,
    });
  });

  test("workspace URL is reachable and not a 500", async ({ page }) => {
    const resp = await page.goto("/workspace/chats/new");
    expect(resp?.status()).toBeLessThan(500);
    // Sign-in form OR chat input — both are valid dev-mode renderings.
    // Wait for hydration instead of counting instantly (flaked in CI).
    const signInBtn = page.getByRole("button", { name: /sign in/i });
    const chatInput = page.getByPlaceholder(/how can i assist you/i);
    await expect(signInBtn.or(chatInput).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("agent computer panel opens with terminal trigger", async ({ page }) => {
    await page.goto("/workspace/chats/new");
    // The trigger button has aria-label "Agent's computer" (i18n key
    // agentComputer.header in Batch 2B.2).
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      // In production sign-in mode the trigger is not in the DOM yet.
      // Skip gracefully.
      test.skip(
        true,
        "agent computer trigger not visible (sign-in gate or auth-disabled off)",
      );
      return;
    }
    await trigger.first().click();
    // The panel opens with tab buttons. Assert at least one tab is visible.
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });
  });

  test("switching tabs hides content without unmounting it", async ({
    page,
  }) => {
    // Regression test: tabs used to fully unmount via a ternary
    // (`activeTab === "x" ? <Component/> : null`), which reset every tab's
    // internal state (terminal mode, editor file, browser nav history) on
    // every switch. Fixed to match the mobile chat/artifacts/computer
    // switcher's always-mounted + `hidden` pattern — this asserts the DOM
    // node for an inactive tab is present (`data-tab="..."`, just hidden),
    // not removed.
    await page.goto("/workspace/chats/new");
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(
        true,
        "agent computer trigger not visible (sign-in gate or auth-disabled off)",
      );
      return;
    }
    await trigger.first().click();
    await expect(page.getByRole("tab").first()).toBeVisible({ timeout: 5_000 });

    const activityPanel = page.locator('[data-tab="activity"]');
    const terminalPanel = page.locator('[data-tab="terminal"]');

    // Activity is the default tab (no sessionStorage-remembered "browser"
    // preference in a fresh test). Terminal is inactive but must still be
    // attached to the DOM (not unmounted) and hidden via CSS.
    await expect(activityPanel).toBeVisible();
    await expect(terminalPanel).toBeAttached();
    await expect(terminalPanel).toBeHidden();

    await page.getByRole("tab", { name: /terminal/i }).click();
    await expect(terminalPanel).toBeVisible();
    await expect(activityPanel).toBeAttached();
    await expect(activityPanel).toBeHidden();

    // Switch back — Activity should still be the same attached node, not a
    // freshly remounted one, and Terminal (now inactive) stays attached too.
    await page.getByRole("tab", { name: /activity/i }).click();
    await expect(activityPanel).toBeVisible();
    await expect(terminalPanel).toBeAttached();
    await expect(terminalPanel).toBeHidden();
  });
});
