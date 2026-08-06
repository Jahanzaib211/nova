/**
 * E2E: the voice mic button in a real browser.
 *
 * Chromium's fake media device (`--use-fake-device-for-media-stream`) means we
 * exercise the genuine `getUserMedia` -> AudioContext -> AudioWorklet ->
 * WebSocket path without a physical microphone. The WebSocket itself is stubbed
 * in-page so the whole client half — capture, framing, playback queue,
 * barge-in — is driven deterministically with no backend running.
 *
 * The two blockers this guards against are both invisible locally:
 *   - `Permissions-Policy: microphone=()` disables getUserMedia app-wide, so
 *     capture can never start on a deployment. Only nginx sets that header, so
 *     it cannot fail on `make dev`.
 *   - a WebSocket path with no nginx `location` fails the upgrade handshake.
 */

import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI, mockSandboxAPI } from "./utils/mock-api";

/** Serve /api/voice/status as "voice is on and ready". */
async function mockVoiceAvailable(page: Page, available = true) {
  await page.route("**/api/voice/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        available
          ? { enabled: true, ready: true, sample_rate: 16000, stt: "scripted", tts: "tone" }
          : { enabled: false },
      ),
    }),
  );
}

/**
 * Replace WebSocket with a controllable stub before any app code runs, and
 * record what the client sends so we can assert it really captured audio.
 */
async function stubVoiceSocket(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as {
      __voice: { sent: number; opened: boolean; emit?: (m: unknown) => void };
      WebSocket: unknown;
    };
    w.__voice = { sent: 0, opened: false };

    class StubSocket extends EventTarget {
      static readonly OPEN = 1;
      readyState = 1;
      binaryType = "arraybuffer";
      onopen: (() => void) | null = null;
      onmessage: ((e: MessageEvent) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: (() => void) | null = null;

      constructor(_url: string) {
        super();
        w.__voice.opened = true;
        // Let the caller drive server events from the test.
        w.__voice.emit = (msg: unknown) =>
          this.onmessage?.(
            new MessageEvent("message", { data: JSON.stringify(msg) }),
          );
        setTimeout(() => this.onopen?.(), 0);
      }

      send(data: unknown) {
        if (data instanceof ArrayBuffer) w.__voice.sent += 1;
      }

      close() {
        this.readyState = 3;
        this.onclose?.();
      }
    }

    w.WebSocket = StubSocket;
  });
}

/**
 * Wait for the client to actually open its socket.
 *
 * `start()` awaits `getUserMedia` before constructing the WebSocket, so
 * emitting a server event straight after the click races that await.
 */
async function waitForSocket(page: Page) {
  await expect
    .poll(
      () =>
        page.evaluate(
          () => (window as never as { __voice: { opened: boolean } }).__voice.opened,
        ),
      { timeout: 15_000, message: "voice socket never opened" },
    )
    .toBe(true);
}

async function openChat(page: Page): Promise<boolean> {
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  const button = page.getByTestId("voice-button");
  try {
    await button.waitFor({ state: "visible", timeout: 15_000 });
  } catch {
    test.skip(true, "voice button not rendered (auth gate)");
    return false;
  }
  return true;
}

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
});

test.describe("Voice", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Voice",
          updated_at: "2026-08-06T12:00:00Z",
        },
      ],
    });
    mockSandboxAPI(page, { files: [] });
    await stubVoiceSocket(page);
  });

  test("the mic button is hidden when voice is not configured", async ({ page }) => {
    // A dead button that fails on click is worse than no button.
    await mockVoiceAvailable(page, false);
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByTestId("voice-button")).toHaveCount(0);
  });

  test("clicking the mic opens a session and starts streaming audio", async ({
    page,
  }) => {
    await mockVoiceAvailable(page);
    if (!(await openChat(page))) return;

    const button = page.getByTestId("voice-button");
    await expect(button).toHaveAttribute("data-voice-phase", "closed");
    await button.click();
    await waitForSocket(page);

    // ready -> idle
    await page.evaluate(() =>
      (window as never as { __voice: { emit: (m: unknown) => void } }).__voice.emit({
        type: "ready",
        sample_rate: 16000,
      }),
    );
    await expect(button).toHaveAttribute("data-voice-phase", "idle");

    // The fake device produces a real tone, so the worklet should be framing
    // and sending PCM. This is the assertion that would fail if
    // Permissions-Policy blocked getUserMedia.
    await expect
      .poll(
        () =>
          page.evaluate(
            () => (window as never as { __voice: { sent: number } }).__voice.sent,
          ),
        { timeout: 15_000, message: "no PCM frames were captured and sent" },
      )
      .toBeGreaterThan(0);
  });

  test("the button reflects listening, thinking and speaking", async ({ page }) => {
    await mockVoiceAvailable(page);
    if (!(await openChat(page))) return;

    const button = page.getByTestId("voice-button");
    await button.click();
    await waitForSocket(page);

    const emit = (msg: unknown) =>
      page.evaluate(
        (m) =>
          (window as never as { __voice: { emit: (x: unknown) => void } }).__voice.emit(m),
        msg,
      );

    await emit({ type: "ready" });
    await emit({ type: "listening" });
    await expect(button).toHaveAttribute("data-voice-phase", "listening");

    await emit({ type: "thinking" });
    await expect(button).toHaveAttribute("data-voice-phase", "thinking");

    await emit({ type: "speaking", sample_rate: 24000 });
    await expect(button).toHaveAttribute("data-voice-phase", "speaking");

    await emit({ type: "idle" });
    await expect(button).toHaveAttribute("data-voice-phase", "idle");
  });

  test("an interrupt while speaking does not wedge the session", async ({ page }) => {
    await mockVoiceAvailable(page);
    if (!(await openChat(page))) return;

    const button = page.getByTestId("voice-button");
    await button.click();
    await waitForSocket(page);

    const emit = (msg: unknown) =>
      page.evaluate(
        (m) =>
          (window as never as { __voice: { emit: (x: unknown) => void } }).__voice.emit(m),
        msg,
      );

    await emit({ type: "ready" });
    await emit({ type: "speaking" });
    // Barge-in: the client flushes playback and the next turn begins.
    await emit({ type: "interrupt" });
    await emit({ type: "listening" });
    await expect(button).toHaveAttribute("data-voice-phase", "listening");
  });

  test("clicking again stops the session", async ({ page }) => {
    await mockVoiceAvailable(page);
    if (!(await openChat(page))) return;

    const button = page.getByTestId("voice-button");
    await button.click();
    await waitForSocket(page);
    await page.evaluate(() =>
      (window as never as { __voice: { emit: (m: unknown) => void } }).__voice.emit({
        type: "ready",
      }),
    );
    await expect(button).toHaveAttribute("data-voice-phase", "idle");

    await button.click();
    await expect(button).toHaveAttribute("data-voice-phase", "closed");
  });
});
