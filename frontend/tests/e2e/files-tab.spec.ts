/**
 * E2E spec: the Files tab of the Agent's Computer.
 *
 * Drives the real component in a real browser against a mocked
 * /api/sandbox/files. The unit tests cover `selectTreeFiles`/`treePathOf` as
 * pure functions; this covers what the user actually sees — that a file is
 * rendered exactly once, that colliding paths both survive, that a
 * file-and-folder of the same name doesn't swallow its subtree, and that the
 * per-file download button points at the artifacts endpoint.
 *
 * Each of these is a bug that shipped:
 *   - presented deliverables rendered in BOTH Outputs and the tree
 *   - `workspace/outputs/x` and `outputs/x` collapsed onto one tree node, so
 *     one file silently vanished while the header count still counted both
 *   - a node with a file AND children rendered as a lone file row, hiding
 *     every descendant
 */

import { expect, test, type Page } from "@playwright/test";

import {
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  mockSandboxAPI,
  sandboxFile,
} from "./utils/mock-api";

const PRESENTED = "/mnt/user-data/outputs/report.md";

const FILES = [
  // The collision pair: same tree path before the fix.
  sandboxFile("/mnt/user-data/workspace/outputs/report.md", 111),
  sandboxFile("/mnt/user-data/outputs/report.md", 222),
  // A file and a folder sharing a name.
  sandboxFile("/mnt/user-data/workspace/build", 333),
  sandboxFile("/mnt/user-data/workspace/build/out.js", 444),
  // An ordinary nested file.
  sandboxFile("/mnt/user-data/workspace/src/index.ts", 555),
  // An upload.
  sandboxFile("/mnt/user-data/uploads/notes.txt", 666),
];

async function openFilesTab(page: Page): Promise<boolean> {
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  const trigger = page.getByRole("button", { name: /agent's computer/i });
  if ((await trigger.count()) === 0) {
    test.skip(true, "agent computer trigger not visible (auth gate)");
    return false;
  }
  await trigger.first().click();
  // NB: not `exact: true` — the tab renders a file-count badge beside the
  // label, so its accessible name is "Files 6", not "Files".
  await page.getByRole("tab", { name: /^files/i }).click();
  await expect(page.locator('[data-tab="files"]')).toBeVisible();
  // Wait for the sandbox file list to actually land before asserting on it —
  // the panel mounts before /api/sandbox/files resolves, and every assertion
  // below is about rendered rows.
  await expect(page.locator('[data-tab="files"] a[download]').first()).toBeVisible({
    timeout: 30_000,
  });
  return true;
}

test.describe("Files tab", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Files tab fixture",
          updated_at: "2026-08-05T12:00:00Z",
          artifacts: [PRESENTED],
        },
      ],
    });
    mockSandboxAPI(page, { files: FILES });
  });

  test("a presented deliverable is rendered exactly once", async ({ page }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');

    // `report.md` is presented (Outputs) and also on disk (tree). Before the
    // fix it appeared in both sections.
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();
    const rows = filesTab.getByText("report.md", { exact: true });
    // Exactly two: one Outputs row, one tree row for the *workspace* copy
    // (a different file that merely shares a basename) — never three.
    await expect(rows).toHaveCount(2);
  });

  test("both colliding paths survive — no file is silently lost", async ({
    page,
  }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();

    // The workspace copy is disambiguated under a `workspace/` folder, so it
    // no longer overwrites the outputs-mount copy.
    await expect(
      filesTab.getByText("workspace/", { exact: true }),
    ).toBeVisible();
  });

  test("a file and a folder with the same name both render", async ({
    page,
  }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();

    // `build` exists as a file AND as a directory containing out.js. The old
    // render short-circuited on node.file and hid the subtree entirely.
    await expect(filesTab.getByText("build", { exact: true })).toBeVisible();
    await expect(filesTab.getByText("build/", { exact: true })).toBeVisible();
    await expect(filesTab.getByText("out.js", { exact: true })).toBeVisible();
  });

  test("the header count matches the number of rendered file rows", async ({
    page,
  }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();

    // The count badge previously counted files the tree had dropped.
    const sizeBadges = filesTab.locator("text=/^\\d+(\\.\\d+)?(B|KB)$/");
    // 6 files on disk, 1 of them presented (moved to Outputs) => 5 tree rows.
    await expect(sizeBadges).toHaveCount(5);
  });
});

test.describe("Files tab downloads", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Download fixture",
          updated_at: "2026-08-05T12:00:00Z",
          artifacts: [PRESENTED],
        },
      ],
    });
    mockSandboxAPI(page, { files: FILES });
  });

  test("every file row exposes a download link to the artifacts endpoint", async ({
    page,
  }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();

    const links = filesTab.locator('a[download]');
    // 5 tree rows + 1 Outputs row.
    await expect(links).toHaveCount(6);

    // Must target the artifacts endpoint (streams real bytes, ownership-checked,
    // sets Content-Disposition) — NOT /api/sandbox/file, which returns JSON and
    // corrupts binaries.
    const hrefs = await links.evaluateAll((els) =>
      els.map((e) => (e as HTMLAnchorElement).getAttribute("href") ?? ""),
    );
    for (const href of hrefs) {
      expect(href).toContain(`/api/threads/${MOCK_THREAD_ID}/artifacts`);
      expect(href).toContain("download=true");
      expect(href).not.toContain("/api/sandbox/file");
    }
  });

  test("the download link carries the file's own virtual path", async ({
    page,
  }) => {
    if (!(await openFilesTab(page))) return;
    const filesTab = page.locator('[data-tab="files"]');
    await expect(filesTab.getByText(/^Outputs/)).toBeVisible();

    const hrefs = await filesTab
      .locator("a[download]")
      .evaluateAll((els) =>
        els.map((e) => (e as HTMLAnchorElement).getAttribute("href") ?? ""),
      );
    expect(hrefs.some((h) => h.includes("/mnt/user-data/workspace/src/index.ts"))).toBe(true);
    expect(hrefs.some((h) => h.includes("/mnt/user-data/uploads/notes.txt"))).toBe(true);
    // The presented deliverable's Outputs row links to its outputs path.
    expect(hrefs.some((h) => h.includes(PRESENTED))).toBe(true);
  });
});

test.describe("Agent's Computer regressions", () => {
  test("download buttons are visible without hovering", async ({ page }) => {
    // Reported from live use: "no per file download showing". The buttons were
    // shipped `opacity-0 group-hover:opacity-100`, so they existed in the DOM
    // and passed a toBeVisible() check while being invisible to the user.
    // Playwright treats opacity:0 as visible, so assert the computed value.
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Visible download",
          updated_at: "2026-08-05T12:00:00Z",
          artifacts: [PRESENTED],
        },
      ],
    });
    mockSandboxAPI(page, { files: FILES });
    if (!(await openFilesTab(page))) return;

    const opacities = await page
      .locator('[data-tab="files"] a[download]')
      .evaluateAll((els) =>
        els.map((e) => getComputedStyle(e as HTMLElement).opacity),
      );
    expect(opacities.length).toBeGreaterThan(0);
    for (const o of opacities) expect(Number(o)).toBeGreaterThan(0.1);
  });

  test("static preview still renders while a dev server is starting", async ({
    page,
  }) => {
    // Regression: the static-file poll was gated on `!devServer.running`, but
    // the live-preview branch requires `running && url`. A dev server that is
    // up with no URL yet ("Dev server compiling…") fell through to the static
    // path with fetching disabled, so the pane sat blank for the whole compile.
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Compiling",
          updated_at: "2026-08-05T12:00:00Z",
        },
      ],
    });
    mockSandboxAPI(page, {
      files: [sandboxFile("/mnt/user-data/workspace/index.html", 42)],
      fileContent: "<html><body><h1>DELIVERABLE</h1></body></html>",
      fileExists: true,
      devStatus: { running: true, status: "starting", port: null, url: null },
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const trigger = page.getByRole("button", { name: /agent's computer/i });
    if ((await trigger.count()) === 0) {
      test.skip(true, "agent computer trigger not visible (auth gate)");
      return;
    }
    await trigger.first().click();
    await page.getByRole("tab", { name: /^files/i }).click();
    await page
      .locator('[data-tab="files"]')
      .getByText("index.html", { exact: true })
      .click();
    await page.getByRole("tab", { name: /^browser/i }).click();

    // The blob-URL iframe only exists once the file content was actually
    // fetched — which is precisely what the bad gate prevented.
    await expect(
      page.locator('[data-tab="browser"] iframe[src^="blob:"]'),
    ).toBeVisible({ timeout: 30_000 });
  });
});
