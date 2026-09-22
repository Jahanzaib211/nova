import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * /workspace/jobs against a mocked /api/jobs: list, filter, cancel/retry
 * affordances follow the status, the events panel opens, schedules CRUD.
 */

const NOW = "2026-09-19T12:00:00Z";
const job = (
  id: string,
  status: string,
  extra: Record<string, unknown> = {},
) => ({
  id,
  type: "jobs.demo.sleep",
  queue: "default",
  status,
  priority: 0,
  payload: {},
  result: null,
  error: null,
  thread_id: null,
  attempts: 0,
  max_attempts: 5,
  progress_pct: status === "succeeded" ? 100 : 40,
  progress_message: status === "running" ? "slept 2/5s" : null,
  cancel_requested: false,
  schedule_id: null,
  run_after: NOW,
  created_at: NOW,
  started_at: null,
  finished_at: null,
  ...extra,
});

test.describe("jobs page", () => {
  test.beforeEach(async ({ page }) => {
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
    let jobs = [
      job("j-run", "running"),
      job("j-dead", "dead_letter", { error: "boom", attempts: 5 }),
      job("j-ok", "succeeded"),
    ];
    const schedules: Array<Record<string, unknown>> = [];
    await page.route("**/api/jobs?**", (r) => {
      const status = new URL(r.request().url()).searchParams.get("status");
      void r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          jobs: status ? jobs.filter((j) => j.status === status) : jobs,
          limit: 100,
          offset: 0,
        }),
      });
    });
    await page.route("**/api/jobs/schedules", (r) => {
      if (r.request().method() === "POST") {
        const body = r.request().postDataJSON() as Record<string, unknown>;
        const created = {
          id: "s1",
          ...body,
          queue: "default",
          payload: body.payload ?? {},
          enabled: true,
          last_enqueued_for: null,
          next_run_at: NOW,
          created_at: NOW,
        };
        schedules.push(created);
        return r.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(created),
        });
      }
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ schedules }),
      });
    });
    await page.route("**/api/jobs/schedules/*", (r) => {
      if (r.request().method() === "DELETE") {
        schedules.length = 0;
        return r.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, status: "deleted" }),
        });
      }
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...schedules[0], enabled: false }),
      });
    });
    await page.route("**/api/jobs/*/events?**", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: "j-ok",
          status: "succeeded",
          events: [
            { seq: 1, type: "enqueued", payload: {}, created_at: NOW },
            { seq: 2, type: "succeeded", payload: {}, created_at: NOW },
          ],
        }),
      }),
    );
    await page.route("**/api/jobs/*/cancel", (r) => {
      jobs = jobs.map((j) =>
        j.id === "j-run" ? { ...j, cancel_requested: true } : j,
      );
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, status: "running" }),
      });
    });
    await page.route("**/api/jobs/*/retry", (r) => {
      jobs = jobs.map((j) =>
        j.id === "j-dead"
          ? { ...j, status: "queued", attempts: 0, error: null }
          : j,
      );
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, status: "queued" }),
      });
    });
  });

  test("lists jobs with status-appropriate actions and live events", async ({
    page,
  }) => {
    await page.goto("/workspace/jobs");
    const rows = page.getByTestId("job-row");
    await expect(rows).toHaveCount(3);
    await expect(
      rows
        .filter({ has: page.locator('[data-status="running"]') })
        .getByRole("button", { name: "Cancel" }),
    ).toBeVisible();
    await expect(
      rows
        .filter({ has: page.locator('[data-status="dead_letter"]') })
        .getByRole("button", { name: "Retry" }),
    ).toBeVisible();
    await expect(
      rows
        .filter({ has: page.locator('[data-status="succeeded"]') })
        .getByRole("button", { name: /cancel|retry/i }),
    ).toHaveCount(0);
    await rows
      .filter({ has: page.locator('[data-status="succeeded"]') })
      .getByRole("button", { name: "Show events" })
      .click();
    await expect(page.getByTestId("job-events")).toContainText("succeeded");
    // sidebar entry is present because the server declared jobs=true
    await expect(page.getByRole("link", { name: "Jobs" })).toBeVisible();
  });

  test("cancel and retry round-trip through the API", async ({ page }) => {
    await page.goto("/workspace/jobs");
    await page
      .getByTestId("job-row")
      .filter({ has: page.locator('[data-status="dead_letter"]') })
      .getByRole("button", { name: "Retry" })
      .click();
    await expect(page.getByText("Job re-queued")).toBeVisible();
    await expect(
      page
        .getByTestId("job-row")
        .filter({ has: page.locator('[data-status="queued"]') }),
    ).toHaveCount(1);
    await page
      .getByTestId("job-row")
      .filter({ has: page.locator('[data-status="running"]') })
      .getByRole("button", { name: "Cancel" })
      .click();
    await expect(page.getByText("Cancellation requested")).toBeVisible();
  });

  test("schedules can be created and deleted", async ({ page }) => {
    await page.goto("/workspace/jobs");
    const form = page.getByRole("form", { name: "Add schedule" });
    await form.getByPlaceholder("Name").fill("nightly");
    await form.getByPlaceholder("Job type").fill("jobs.demo.sleep");
    await form.getByPlaceholder("Payload (JSON)").fill("not json");
    await form.getByRole("button", { name: "Add schedule" }).click();
    await expect(
      page.getByText("Payload must be a JSON object."),
    ).toBeVisible();
    await form.getByPlaceholder("Payload (JSON)").fill('{"seconds": 2}');
    await form.getByRole("button", { name: "Add schedule" }).click();
    await expect(page.getByText("Schedule created")).toBeVisible();
    await expect(page.getByTestId("job-schedules")).toContainText("nightly");
    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Schedule deleted")).toBeVisible();
  });
});
