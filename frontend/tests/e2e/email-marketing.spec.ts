import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * /workspace/email against a mocked /api/em: honest empty states, list
 * creation, a campaign whose preflight blocks send, a sending campaign with
 * live stats, and Settings › Email with unconfigured bridges.
 */

const NOW = "2026-09-19T12:00:00Z";
const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

function capabilities(features: Record<string, boolean>) {
  return json({
    features,
    skills: [],
    tools: [],
    hooks: [],
    subagents: [],
    circuits: [],
    server: {},
  });
}

test.describe("email marketing", () => {
  test("empty states, create a list, preflight blocks a draft, live campaign", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/runtime/capabilities", (r) =>
      r.fulfill(capabilities({ email_marketing: true, jobs: true })),
    );
    const lists: Array<Record<string, unknown>> = [];
    await page.route("**/api/em/lists", (r) => {
      if (r.request().method() === "POST") {
        const body = r.request().postDataJSON() as { name: string };
        lists.push({
          id: "l1",
          name: body.name,
          description: null,
          member_count: 0,
          created_at: NOW,
          updated_at: NOW,
        });
        return r.fulfill(json(lists[0], 201));
      }
      return r.fulfill(json({ lists }));
    });
    await page.route("**/api/em/templates", (r) =>
      r.fulfill(
        json({
          templates: [
            {
              id: "t1",
              name: "Welcome",
              subject: "Hi",
              html: "<p>x</p>",
              text: null,
              version: 1,
              created_at: NOW,
              updated_at: NOW,
            },
          ],
        }),
      ),
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
              name: "Draft one",
              list_id: "l1",
              template_id: "t1",
              from_email: "n@x.y",
              from_name: "Nova",
              reply_to: null,
              status: "draft",
              scheduled_at: null,
              throttle_per_minute: null,
              stats: {},
              job_id: null,
              error: null,
              created_at: NOW,
              started_at: null,
              finished_at: null,
            },
            {
              id: "c2",
              name: "Live one",
              list_id: "l1",
              template_id: "t1",
              from_email: "n@x.y",
              from_name: "Nova",
              reply_to: null,
              status: "sending",
              scheduled_at: null,
              throttle_per_minute: null,
              stats: {
                recipients: 10,
                queued: 4,
                sent: 6,
                opened: 2,
                clicked: 1,
              },
              job_id: "j1",
              error: null,
              created_at: NOW,
              started_at: NOW,
              finished_at: null,
            },
          ],
        }),
      ),
    );
    await page.route("**/api/em/campaigns/c1/preflight", (r) =>
      r.fulfill(
        json({
          ok: false,
          problems: [
            "no recipients (every member is unsubscribed or suppressed)",
          ],
          recipients: 0,
          suppressed: 0,
          list: "News",
          template: "Welcome",
        }),
      ),
    );
    await page.route("**/api/em/campaigns/c1/events?**", (r) =>
      r.fulfill(json({ events: [] })),
    );
    await page.route("**/api/em/campaigns/c2/stats", (r) =>
      r.fulfill(
        json({
          recipients: 10,
          queued: 4,
          sending: 0,
          sent: 6,
          deferred: 0,
          failed: 0,
          suppressed: 0,
          opened: 2,
          clicked: 1,
          bounced_hard: 0,
          bounced_soft: 0,
          complained: 0,
          unsubscribed: 0,
        }),
      ),
    );
    await page.route("**/api/em/campaigns/c2/events?**", (r) =>
      r.fulfill(
        json({
          events: [
            {
              id: 1,
              type: "sent",
              campaign_id: "c2",
              send_id: "s1",
              contact_id: "k1",
              payload: {},
              created_at: NOW,
            },
          ],
        }),
      ),
    );

    await page.goto("/workspace/email");
    await expect(page.getByRole("link", { name: "Email" })).toBeVisible();
    const rows = page.getByTestId("em-campaign-row");
    await expect(rows).toHaveCount(2);

    // Draft: preflight is honest about why it cannot send, and the button is disabled.
    await rows
      .filter({ hasText: "Draft one" })
      .getByRole("button", { name: /^Draft one/ })
      .click();
    await expect(page.getByTestId("em-preflight")).toContainText(
      "Not ready to send",
    );
    await expect(page.getByTestId("problems")).toContainText("no recipients");
    await expect(page.getByRole("button", { name: "Send now" })).toBeDisabled();

    // Live: progress from sends, rates only against sent.
    await rows
      .filter({ hasText: "Live one" })
      .getByRole("button", { name: /^Live one/ })
      .click();
    await expect(page.getByTestId("em-campaign-progress")).toContainText(
      "60% delivered · 4 left",
    );
    await expect(page.getByTestId("em-campaign-detail").last()).toContainText(
      "33.3%",
    );

    // Lists tab: empty state then creation.
    await page.getByRole("tab", { name: "Lists" }).click();
    await expect(page.getByTestId("em-lists-empty")).toBeVisible();
    const form = page.getByRole("form", { name: "Create list" });
    await form.getByPlaceholder("List name").fill("News");
    await form.getByRole("button", { name: "Create list" }).click();
    await expect(page.getByText("List created")).toBeVisible();
    await expect(page.getByTestId("em-list-row")).toContainText("News");
    await expect(page.getByTestId("em-list-row")).toContainText("0 members");

    // Contacts + suppressions are honest when empty.
    await page.getByRole("tab", { name: "Contacts" }).click();
    await expect(page.getByTestId("em-contacts-empty")).toBeVisible();
    await page.getByRole("tab", { name: "Suppressions" }).click();
    await expect(page.getByTestId("em-suppressions-empty")).toBeVisible();
  });

  test("settings shows each bridge as not configured with the reason", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/runtime/capabilities", (r) =>
      r.fulfill(capabilities({ email_marketing: true })),
    );
    await page.route("**/api/em/lists", (r) => r.fulfill(json({ lists: [] })));
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
              configured: true,
              problems: [],
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
    await page.goto("/workspace/settings#email");
    await expect(page.getByTestId("em-bridge-mailcow")).toHaveAttribute(
      "data-configured",
      "false",
    );
    await expect(page.getByTestId("em-bridge-mailcow")).toContainText(
      "MAILCOW_API_KEY is not set",
    );
    await expect(page.getByTestId("em-bridge-twenty")).toHaveAttribute(
      "data-configured",
      "true",
    );
    // Only a configured bridge gets its action panel.
    await expect(
      page.getByRole("button", { name: "Push to Twenty" }),
    ).toBeVisible();
    await expect(page.getByRole("form", { name: "Ensure sender" })).toHaveCount(
      0,
    );
  });

  test("is hidden while the server flag is off", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/runtime/capabilities", (r) =>
      r.fulfill(capabilities({ email_marketing: false })),
    );
    await page.goto("/workspace/settings#account");
    await expect(page.getByRole("link", { name: "Email" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Email" })).toHaveCount(0);
  });
});
