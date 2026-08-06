/** Screenshot capture of the changed UI. Not a gate — run on demand. */
import { test, type Page } from "@playwright/test";

import {
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  mockSandboxAPI,
  sandboxFile,
} from "./utils/mock-api";

const PRESENTED = "/mnt/user-data/outputs/report.md";
const FILES = [
  sandboxFile("/mnt/user-data/workspace/outputs/report.md", 111),
  sandboxFile("/mnt/user-data/outputs/report.md", 222),
  sandboxFile("/mnt/user-data/workspace/build", 333),
  sandboxFile("/mnt/user-data/workspace/build/out.js", 444),
  sandboxFile("/mnt/user-data/workspace/src/index.ts", 555),
  sandboxFile("/mnt/user-data/uploads/notes.txt", 666),
];

async function setup(page: Page, voice = true) {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Nova UI",
        updated_at: "2026-08-06T12:00:00Z",
        artifacts: [PRESENTED],
      },
    ],
  });
  mockSandboxAPI(page, { files: FILES });
  await page.route("**/api/voice/status", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        voice ? { enabled: true, ready: true, sample_rate: 16000 } : { enabled: false },
      ),
    }),
  );
}

// Opt-in: this writes PNGs and is for eyeballing the UI, not for gating.
//   CAPTURE_SCREENS=1 E2E_PORT=3111 CI=1 pnpm exec playwright test tests/e2e/zz-screens.spec.ts
test.skip(!process.env.CAPTURE_SCREENS, "set CAPTURE_SCREENS=1 to capture UI screenshots");

test.use({
  viewport: { width: 1440, height: 900 },
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
});
test.setTimeout(90_000);

test("capture the changed UI", async ({ page }) => {
  await setup(page);
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

  // Composer with the new mic button next to the paperclip.
  const mic = page.getByTestId("voice-button");
  await mic.waitFor({ state: "visible", timeout: 20_000 });
  await page.screenshot({ path: "screens/01-composer-mic.png" });

  const composer = page.locator("form").first();
  if (await composer.count()) {
    await composer.screenshot({ path: "screens/02-composer-closeup.png" });
  }

  // Files tab: download buttons, the workspace/ disambiguation, build as both
  // a file and a folder.
  const trigger = page.getByRole("button", { name: /agent's computer/i });
  await trigger.first().click();
  await page.getByRole("tab", { name: /^files/i }).click();
  const filesTab = page.locator('[data-tab="files"]');
  await filesTab
    .locator("a[download]")
    .first()
    .waitFor({ state: "visible", timeout: 30_000 });
  await page.screenshot({ path: "screens/03-files-tab-full.png" });
  await filesTab.screenshot({ path: "screens/04-files-tab.png" });

  // Browser tab (the preview surface the nginx fix unblocked).
  await page.getByRole("tab", { name: /^browser/i }).click();
  await page.waitForTimeout(1500);
  await page.locator('[data-tab="browser"]').screenshot({
    path: "screens/05-browser-tab.png",
  });

  // Terminal tab (session persistence fix).
  await page.getByRole("tab", { name: /^terminal/i }).click();
  await page.waitForTimeout(1000);
  await page.locator('[data-tab="terminal"]').screenshot({
    path: "screens/06-terminal-tab.png",
  });
});

test("capture the live voice panel in each phase", async ({ page }) => {
  await setup(page);
  await page.addInitScript(() => {
    const w = window as unknown as {
      __voice: { sent: number; opened: boolean; emit?: (m: unknown) => void };
      WebSocket: unknown;
    };
    w.__voice = { sent: 0, opened: false };
    class StubSocket extends EventTarget {
      static readonly OPEN = 1;
      readyState = 1; binaryType = "arraybuffer";
      onopen: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: (() => void) | null = null;
      constructor(_u: string) {
        super();
        w.__voice.opened = true;
        w.__voice.emit = (m: unknown) => this.onmessage?.({ data: JSON.stringify(m) });
        setTimeout(() => this.onopen?.(), 0);
      }
      send(d: unknown) { if (d instanceof ArrayBuffer) w.__voice.sent += 1; }
      close() { this.readyState = 3; this.onclose?.(); }
    }
    w.WebSocket = StubSocket;
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  const mic = page.getByTestId("voice-button");
  await mic.waitFor({ state: "visible", timeout: 20_000 });
  await mic.click();
  await page.waitForFunction(
    () => (window as never as { __voice?: { opened: boolean } }).__voice?.opened === true,
    { timeout: 15_000 },
  );

  const emit = (m: unknown) =>
    page.evaluate(
      (x) =>
        (window as never as { __voice: { emit: (y: unknown) => void } }).__voice.emit(x),
      m,
    );
  const shot = async (name: string) => {
    await page.waitForTimeout(600);
    const panel = page.getByTestId("voice-panel");
    await panel.screenshot({ path: `screens/${name}` });
  };

  await emit({ type: "ready", sample_rate: 16000 });
  await shot("10-voice-idle.png");

  await emit({ type: "listening" });
  await emit({ type: "transcript", text: "what's the status of the deploy", final: true });
  await shot("11-voice-listening.png");

  await emit({ type: "thinking" });
  await shot("12-voice-thinking.png");

  await emit({ type: "speaking", sample_rate: 24000 });
  await emit({ type: "assistant", text: "The deploy is live on both origins and all tests are green." });
  await shot("13-voice-speaking.png");

  await page.screenshot({ path: "screens/14-voice-composer-full.png" });
});

// The pill still renders when voice is off — it just offers the setup hint.
test("capture the voice pill in its not-yet-enabled state", async ({ page }) => {
  await setup(page, false);
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await page.waitForTimeout(3000);
  await page.screenshot({ path: "screens/07-voice-disabled.png" });
});
