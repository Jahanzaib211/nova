import { describe, expect, it } from "vitest";

import {
  describeAction,
  formatAttention,
  frameForStep,
  recordingFrameUrl,
  stepForFrame,
  type RecordingAction,
} from "@/core/browser/api";

const actions: RecordingAction[] = [
  {
    tool: "browser_navigate",
    t: 0.5,
    ok: true,
    frame: 2,
    url: "https://x.test/",
  },
  {
    tool: "browser_input",
    t: 2,
    ok: true,
    frame: null,
    selector: "#pw",
    text_chars: 8,
  },
  { tool: "browser_click", t: 3, ok: false, frame: 6, selector: "#go" },
];

describe("replay sync", () => {
  it("maps a frame to the latest step at or before it", () => {
    expect(stepForFrame(actions, 0)).toBe(-1);
    expect(stepForFrame(actions, 2)).toBe(0);
    expect(stepForFrame(actions, 5)).toBe(0);
    expect(stepForFrame(actions, 9)).toBe(2);
  });

  it("maps a step without its own still to the nearest earlier frame", () => {
    expect(frameForStep(actions, 1, 10)).toBe(2);
    expect(frameForStep(actions, 2, 10)).toBe(6);
    expect(frameForStep(actions, 2, 5)).toBe(2); // out-of-range frame is skipped
  });
});

describe("labels", () => {
  it("never shows typed text, only its length", () => {
    expect(describeAction(actions[1]!)).toBe("input #pw (8 chars)");
    expect(describeAction(actions[0]!)).toBe("navigate https://x.test/");
  });

  it("formats the attention prompt with the host only", () => {
    expect(
      formatAttention("A {kind} check is blocking {url}.", {
        kind: "recaptcha",
        url: "https://login.example.com/a?x=1",
        at: 0,
        resolved_at: null,
      }),
    ).toBe("A recaptcha check is blocking login.example.com.");
  });

  it("encodes recording frame URLs", () => {
    expect(recordingFrameUrl("t1", "rec-1", "f000001.jpg")).toMatch(
      /\/api\/threads\/t1\/browser\/recordings\/rec-1\/frames\/f000001\.jpg$/,
    );
  });
});
