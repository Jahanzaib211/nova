import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AcpTranscriptView } from "@/components/workspace/messages/acp-transcript";
import { I18nProvider } from "@/core/i18n/context";
import {
  applyAcpUpdate,
  isAcpUpdateEvent,
  type AcpUpdateEvent,
} from "@/core/threads/acp-transcript";

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
      text: "Hello",
      statuses: ["permission denied: execute — rm"],
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
