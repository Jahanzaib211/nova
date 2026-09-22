import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AcpTranscriptView,
  RuntimeTranscriptCard,
} from "@/components/workspace/messages/acp-transcript";
import { I18nProvider } from "@/core/i18n/context";
import {
  applyAcpUpdate,
  isAcpUpdateEvent,
  runtimeTranscripts,
  type AcpUpdateEvent,
} from "@/core/threads/acp-transcript";
import { acpTranscriptActivityEvents } from "@/core/threads/activity";

const ev = (over: Partial<AcpUpdateEvent> = {}): AcpUpdateEvent => ({
  type: "acp_update",
  agent: "claude_code",
  session_id: "s-1",
  kind: "text",
  delta: "hi",
  ...over,
});

describe("isAcpUpdateEvent", () => {
  it("accepts the contract shape and rejects anything else", () => {
    expect(isAcpUpdateEvent(ev())).toBe(true);
    expect(isAcpUpdateEvent(ev({ kind: "status" }))).toBe(true);
    expect(isAcpUpdateEvent({ ...ev(), kind: "thought" })).toBe(false);
    expect(isAcpUpdateEvent({ ...ev(), agent: "" })).toBe(false);
    expect(isAcpUpdateEvent({ type: "task_progress" })).toBe(false);
    expect(isAcpUpdateEvent(null)).toBe(false);
  });
});

describe("applyAcpUpdate", () => {
  it("appends text and statuses per agent", () => {
    let s = applyAcpUpdate({}, ev({ delta: "Hel" }));
    s = applyAcpUpdate(s, ev({ delta: "lo" }));
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "permission denied: execute — rm" }),
    );
    s = applyAcpUpdate(s, ev({ agent: "openclaw", delta: "yo" }));
    expect(s.claude_code).toEqual({
      sessionId: "s-1",
      // The permission decision is an action, so the text that follows it
      // would start a new paragraph.
      text: "Hello\n\n",
      statuses: ["permission denied: execute — rm"],
      actionCount: 1,
    });
    expect(s.openclaw?.text).toBe("yo");
  });

  it("starts over when the same agent opens a new session", () => {
    let s = applyAcpUpdate({}, ev({ delta: "old" }));
    s = applyAcpUpdate(s, ev({ session_id: "s-2", delta: "new" }));
    expect(s.claude_code?.text).toBe("new");
    expect(s.claude_code?.sessionId).toBe("s-2");
  });

  it("bounds memory: text is capped and statuses are a ring", () => {
    let s = applyAcpUpdate({}, ev({ delta: "x".repeat(30_000) }));
    expect(s.claude_code?.text.length).toBe(20_000);
    for (let i = 0; i < 60; i++)
      s = applyAcpUpdate(s, ev({ kind: "status", delta: `s${i}` }));
    expect(s.claude_code?.statuses).toHaveLength(50);
    expect(s.claude_code?.statuses[0]).toBe("s10");
  });
});

describe("AcpTranscriptView", () => {
  const render = (node: React.ReactNode) =>
    renderToStaticMarkup(
      <I18nProvider initialLocale="en-US">{node}</I18nProvider>,
    );

  it("says it is starting, never a fake transcript, before the first delta", () => {
    const html = render(
      <AcpTranscriptView transcript={undefined} result={undefined} running />,
    );
    expect(html).toContain("Starting the agent");
    expect(html).not.toContain("acp-transcript");
  });

  it("streams text with statuses while running, then settles on the result", () => {
    const transcript = {
      sessionId: "s-1",
      text: "partial",
      statuses: ["permission denied: execute — rm -rf"],
      actionCount: 1,
    };
    const live = render(
      <AcpTranscriptView transcript={transcript} result={undefined} running />,
    );
    expect(live).toContain("partial");
    expect(live).toContain("permission denied");
    expect(live).toContain('data-running="true"');
    const done = render(
      <AcpTranscriptView
        transcript={transcript}
        result="final answer"
        running={false}
      />,
    );
    expect(done).toContain("final answer");
    expect(done).not.toContain("partial");
  });

  it("renders nothing for a finished call with no output", () => {
    expect(
      render(
        <AcpTranscriptView
          transcript={undefined}
          result={undefined}
          running={false}
        />,
      ),
    ).toBe("");
  });
});

describe("runtime turn (no invoke_acp_agent tool call)", () => {
  const render = (node: React.ReactNode) =>
    renderToStaticMarkup(
      <I18nProvider initialLocale="en-US">{node}</I18nProvider>,
    );

  it("keeps the runtime's announcement when the real session id arrives", () => {
    let s = applyAcpUpdate(
      {},
      ev({
        session_id: "t1",
        kind: "status",
        delta: "runtime: claude_code (plan)",
      }),
    );
    s = applyAcpUpdate(
      s,
      ev({ session_id: "sess-9", kind: "status", delta: "execute: ls" }),
    );
    s = applyAcpUpdate(s, ev({ session_id: "sess-9", delta: "hello" }));
    expect(s.claude_code?.statuses).toEqual([
      "runtime: claude_code (plan)",
      "execute: ls",
    ]);
    expect(s.claude_code?.actionCount).toBe(1);
    expect(s.claude_code?.text).toBe("hello");
  });

  it("selects transcripts that no tool call in this turn is driving", () => {
    const transcripts = applyAcpUpdate(
      {},
      ev({ kind: "status", delta: "execute: ls" }),
    );
    const toolDriven = [
      { type: "human" },
      {
        type: "ai",
        tool_calls: [
          { name: "invoke_acp_agent", args: { agent: "claude_code" } },
        ],
      },
    ];
    expect(runtimeTranscripts(transcripts, toolDriven)).toEqual([]);
    const runtimeDriven = [{ type: "human" }];
    expect(
      runtimeTranscripts(transcripts, runtimeDriven).map(([a]) => a),
    ).toEqual(["claude_code"]);
    // A tool call from an *earlier* turn does not claim this turn's transcript.
    const earlier = [
      {
        type: "ai",
        tool_calls: [
          { name: "invoke_acp_agent", args: { agent: "claude_code" } },
        ],
      },
      { type: "human" },
    ];
    expect(runtimeTranscripts(transcripts, earlier).length).toBe(1);
  });

  it("renders the runtime card with agent name, action count and the streamed text", () => {
    let s = applyAcpUpdate(
      {},
      ev({ kind: "status", delta: "runtime: claude_code (plan)" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "execute: Bash(ls /mnt/user-data)" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "permission denied: edit — write app.py" }),
    );
    s = applyAcpUpdate(s, ev({ delta: "Here is what I found" }));
    const html = render(
      <RuntimeTranscriptCard agent="claude_code" transcript={s.claude_code!} />,
    );
    expect(html).toContain("Claude Code is working on this turn");
    expect(html).toContain("2 actions");
    expect(html).toContain("Bash(ls /mnt/user-data)");
    expect(html).toContain("permission denied");
    expect(html).toContain("Here is what I found");
  });

  it("turns status lines into Agent's Computer activity cards", () => {
    let s = applyAcpUpdate(
      {},
      ev({ kind: "status", delta: "runtime: claude_code (plan)" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "thinking: let me look" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "read: Read(config.yaml)" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "permission denied: execute — rm -rf" }),
    );
    s = applyAcpUpdate(
      s,
      ev({ kind: "status", delta: "execute: Bash(pytest)" }),
    );
    const events = acpTranscriptActivityEvents(s, { running: true });
    expect(events.map((e) => e.status)).toEqual(["done", "error", "running"]);
    expect(events[0]?.summary).toBe("Claude Code · Read(config.yaml)");
    expect(events[0]?.type).toBe("acp_read");
    expect(
      acpTranscriptActivityEvents(s, { running: false }).at(-1)?.status,
    ).toBe("done");
    expect(acpTranscriptActivityEvents(undefined, { running: true })).toEqual(
      [],
    );
  });
});

describe("paragraph breaks", () => {
  it("separates text that resumes after a tool call", () => {
    let s = applyAcpUpdate({}, ev({ delta: "Let me look at the sandbox." }));
    s = applyAcpUpdate(s, ev({ kind: "status", delta: "execute: Bash(ls)" }));
    s = applyAcpUpdate(s, ev({ delta: "It has three files." }));
    expect(s.claude_code?.text).toBe(
      "Let me look at the sandbox.\n\nIt has three files.",
    );
    // Narration (thinking) does not break paragraphs, and no double breaks.
    s = applyAcpUpdate(s, ev({ kind: "status", delta: "thinking: hmm" }));
    s = applyAcpUpdate(s, ev({ kind: "status", delta: "read: Read(a.py)" }));
    s = applyAcpUpdate(s, ev({ kind: "status", delta: "read: Read(b.py)" }));
    expect(s.claude_code?.text).toBe(
      "Let me look at the sandbox.\n\nIt has three files.\n\n",
    );
  });
});
