import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const resultFile = join(here, "last-result.txt");

const email = process.env.E2E_USER_EMAIL;
const password = process.env.E2E_USER_PASSWORD;
if (!email || !password) {
  throw new Error("E2E_USER_EMAIL and E2E_USER_PASSWORD env vars are required");
}

const WATCHED_PATTERNS: RegExp[] = [
  /\/api\/sandbox\/logs/,
  /\/api\/sandbox\/todo/,
  /\/api\/sandbox\/files/,
  /\/api\/sandbox\/dev-status/,
  /\/api\/sandbox\/dev-servers/,
  /\/api\/threads\/[^/]+\/computer-ws/,
  /\/api\/workspace\/[^/]+\/events/,
];

type WatchedHit = {
  url: string;
  method: string;
  status: number;
  ts: string;
};

const watchedHits: WatchedHit[] = [];
const consoleErrors: string[] = [];
const pageErrors: string[] = [];

// page.on("response") never fires for a WebSocket handshake, so the socket that
// carries every subagent event was invisible to this probe. Track it directly.
type SocketRecord = {
  url: string;
  opened: boolean;
  framesIn: number;
  framesOut: number;
  error?: string;
  closed: boolean;
};
const sockets: SocketRecord[] = [];
const isComputerWs = (url: string) =>
  /\/api\/threads\/[^/]+\/computer-ws/.test(url);

function matchesWatched(url: string): boolean {
  return WATCHED_PATTERNS.some((re) => re.test(url));
}

test.describe("live - subagent UI regression probe", () => {
  test("subagent run produces assistant reply + subtask UI without watched 4xx/5xx", async ({
    page,
    context,
    baseURL,
  }) => {
    writeFileSync(resultFile, "", "utf-8");

    const append = (s: string) => writeFileSync(resultFile, s, { flag: "a" });

    const log = (s: string) => {
      append(s + "\n");

      console.log(s);
    };

    // ── Login via the real /api/v1/auth/login/local endpoint ──────────────
    const loginResp = await context.request.post(
      `${baseURL}/api/v1/auth/login/local`,
      {
        form: { username: email, password },
      },
    );
    const loginBody = await loginResp.text();
    expect(
      loginResp.status(),
      `login failed: ${loginResp.status()} ${loginBody}`,
    ).toBe(200);

    // ── Wire up network + error capture ───────────────────────────────────
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        consoleErrors.push(text);
        log(`[console.error] ${text}`);
      }
    });
    page.on("pageerror", (err) => {
      const text = `${err.name}: ${err.message}`;
      pageErrors.push(text);
      log(`[pageerror] ${text}`);
    });
    page.on("websocket", (ws) => {
      const rec: SocketRecord = {
        url: ws.url(),
        opened: true,
        framesIn: 0,
        framesOut: 0,
        closed: false,
      };
      sockets.push(rec);
      log(`[ws open] ${ws.url()}`);
      ws.on("framereceived", () => {
        rec.framesIn += 1;
      });
      ws.on("framesent", () => {
        rec.framesOut += 1;
      });
      ws.on("socketerror", (err) => {
        rec.error = String(err);
        rec.opened = false;
        log(`[ws ERROR] ${ws.url()} -- ${String(err)}`);
      });
      ws.on("close", () => {
        rec.closed = true;
        log(
          `[ws close] ${ws.url()} in=${rec.framesIn} out=${rec.framesOut}` +
            (rec.error ? ` error=${rec.error}` : ""),
        );
      });
    });
    page.on("response", async (resp) => {
      const url = resp.url();
      if (!matchesWatched(url)) return;
      const status = resp.status();
      const hit: WatchedHit = {
        url,
        method: resp.request().method(),
        status,
        ts: new Date().toISOString(),
      };
      watchedHits.push(hit);
      const flag = status >= 400 ? " <-- ERROR" : "";
      log(`[watched ${status}${flag}] ${hit.method} ${url}`);
    });

    // ── Navigate to a fresh chat ─────────────────────────────────────────
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });

    // Terms gate (idempotent - users with tos_accepted_version=NULL see it)
    const gateHeading = page.getByRole("heading", {
      name: /we.{0,3}ve updated our terms/i,
    });
    const gateVisible = await gateHeading
      .waitFor({ state: "visible", timeout: 20_000 })
      .then(() => true)
      .catch(() => false);
    log(`[gate] visible=${gateVisible}`);
    if (gateVisible) {
      await page
        .getByRole("button", { name: /i agree/i })
        .click({ force: true });
      await gateHeading
        .waitFor({ state: "hidden", timeout: 20_000 })
        .catch(() => undefined);
    }

    // ── Send the prompt ──────────────────────────────────────────────────
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 30_000 });
    const promptText =
      "Use the general-purpose subagent to write a short hello.txt file in /tmp and then read it back. Then reply with one short sentence.";
    await textarea.fill(promptText);
    await textarea.press("Enter");
    log(`[prompt submitted]`);

    // ── Wait for an assistant response containing "hello" ─────────────────
    const chat = page.locator("#chat");
    let helloSeen = false;
    try {
      await expect(
        chat.locator("p", { hasText: /hello/i }).first(),
      ).toBeVisible({ timeout: 180_000 });
      helloSeen = true;
    } catch {
      log(`[verify] no assistant reply containing 'hello' within 180s`);
    }

    // ── Wait for at least one subtask card ───────────────────────────────
    // The frontend renders subagent progress as [data-testid="subtask-card"]
    // or .subtask-card. Race both - whichever resolves first wins.
    const subtask = page
      .locator('[data-testid="subtask-card"], .subtask-card')
      .first();
    let subtaskSeen = false;
    try {
      await expect(subtask).toBeVisible({ timeout: 60_000 });
      subtaskSeen = true;
    } catch {
      log(`[verify] no subtask card appeared within 60s`);
    }

    // Give a brief grace period for any trailing watched requests to land.
    await page.waitForTimeout(5_000);

    // ── Summarize ────────────────────────────────────────────────────────
    // Endpoints that answer 404 simply because this thread never provisioned a
    // sandbox. `/api/workspace/{id}/events` resolves the sandbox work dir and
    // 404s when it is absent, exactly like the /api/sandbox/* polls — a run that
    // needed no sandbox is not a defect. A 403 from either (workspace
    // intelligence disabled, or ownership refused) is a different animal and
    // must still fail the run.
    const isSandboxProbe = (url: string) =>
      url.includes("/api/sandbox/") ||
      /\/api\/workspace\/[^/]+\/events/.test(url);
    const watchedErrors = watchedHits.filter((h) => {
      if (h.status < 400) return false;
      // "no sandbox provisioned for this thread" is a normal 404 the panel
      // polls through; it is not a defect. Everything else still counts.
      if (h.status === 404 && isSandboxProbe(h.url)) return false;
      return true;
    });

    log("");
    log("=== watched hits ===");
    if (watchedHits.length === 0) {
      log("(none)");
    } else {
      for (const h of watchedHits) {
        log(`  ${h.method} ${h.status} ${h.url}`);
      }
    }
    const ignored404s = watchedHits.filter(
      (h) => h.status === 404 && isSandboxProbe(h.url),
    );
    log("");
    log(`=== ignored sandbox-not-provisioned 404s: ${ignored404s.length} ===`);
    log("");
    log(`=== watched errors (>=400): ${watchedErrors.length} ===`);
    for (const h of watchedErrors) {
      log(`  ${h.method} ${h.status} ${h.url}`);
    }
    log("");
    log(`=== console.error count: ${consoleErrors.length} ===`);
    for (const e of consoleErrors) log(`  ${e}`);
    log("");
    log(`=== pageerror count: ${pageErrors.length} ===`);
    for (const e of pageErrors) log(`  ${e}`);
    log("");
    const computerSockets = sockets.filter((w) => isComputerWs(w.url));
    const liveComputerSocket = computerSockets.find(
      (w) => w.opened && !w.error,
    );

    log("");
    log(`=== websockets seen: ${sockets.length} ===`);
    for (const w of sockets) {
      log(
        `  ${w.opened && !w.error ? "OPEN " : "FAILED"} in=${w.framesIn} out=${w.framesOut}` +
          `${w.error ? ` error=${w.error}` : ""} ${w.url}`,
      );
    }
    log("");
    log(`subtask_card_seen=${subtaskSeen ? "PASS" : "FAIL"}`);
    log(`hello_reply_seen=${helloSeen ? "PASS" : "FAIL"}`);
    log(
      `computer_ws_live=${liveComputerSocket ? "PASS" : "FAIL"} ` +
        `(${computerSockets.length} attempt(s))`,
    );

    // ── Hard assertions ──────────────────────────────────────────────────
    // 1. No watched endpoint may return >=400 during the run
    expect(
      watchedErrors,
      `${watchedErrors.length} watched endpoint(s) returned >=400 (see last-result.txt)`,
    ).toEqual([]);
    // 2. The subagent UI must actually render
    expect(
      subtaskSeen,
      "no [data-testid=subtask-card] or .subtask-card appeared",
    ).toBe(true);
    // 3. The agent must reply with something mentioning hello
    expect(helloSeen, "no assistant reply containing 'hello' arrived").toBe(
      true,
    );
    // 4. The computer-ws socket must open AND carry frames. Asserting only the
    //    absence of 4xx missed this entirely: the handshake never reaches
    //    page.on("response"), and the SSE leg keeps rendering the subtask card,
    //    so a totally dead socket still looked like a passing run.
    expect(
      computerSockets.length,
      "the panel never attempted a computer-ws connection",
    ).toBeGreaterThan(0);
    expect(
      liveComputerSocket,
      `no computer-ws socket opened and received frames; attempts: ${JSON.stringify(
        computerSockets,
      )}`,
    ).toBeTruthy();

    log("FINAL: PASS");
  });
});
