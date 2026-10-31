import { describe, expect, it } from "vitest";

import { applySandboxFrame, type SandboxEvent } from "@/core/sandbox/hooks";

/**
 * One streamed command is one Terminal entry.
 *
 * `bash` used to write a single log line after the command exited, so a
 * five-minute `npm install` appeared as one row at the very end. It now writes
 * an opening frame, a frame per output change, and a closing frame, all sharing
 * an `id`.
 *
 * The frames must be folded **at ingest**, before the 200-event display window.
 * Pushed individually, a chatty command walks its own opening frame out of that
 * window — the `$ npm install` line the user is reading disappears while its
 * output is still arriving.
 */

const MAX_EVENTS = 200;

function frame(over: Partial<SandboxEvent>): SandboxEvent {
  return {
    ts: "14:23:01",
    type: "bash",
    path: null,
    summary: "",
    output: "",
    ...over,
  };
}

/** The reduce the hook performs on each animation frame. */
function ingest(frames: SandboxEvent[], start: SandboxEvent[] = []) {
  let list = start;
  for (const f of frames) list = applySandboxFrame(list, f);
  return list;
}

describe("applySandboxFrame", () => {
  it("leaves a line without an id exactly as it was", () => {
    const plain = frame({ summary: "Wrote 3 bytes", type: "write_file" });
    const out = ingest([plain, plain]);
    expect(out).toHaveLength(2);
    expect(out[0]!.summary).toBe("Wrote 3 bytes");
  });

  it("folds delta frames into the entry that opened them", () => {
    const out = ingest([
      frame({ id: "a", summary: "$ npm install", state: "running" }),
      frame({ id: "a", delta: "added 12 ", state: "running" }),
      frame({ id: "a", delta: "packages\n", state: "running" }),
    ]);

    expect(out).toHaveLength(1);
    expect(out[0]!.summary).toBe("$ npm install");
    expect(out[0]!.output).toBe("added 12 packages\n");
    expect(out[0]!.state).toBe("running");
  });

  it("replaces the body on a redraw instead of appending it", () => {
    // `pip install` rewrites one line via \r; appending would print it twice.
    const out = ingest([
      frame({ id: "a", summary: "$ pip install x", state: "running" }),
      frame({ id: "a", replace: "Downloading  50%", state: "running" }),
      frame({ id: "a", replace: "Downloading 100%", state: "running" }),
    ]);

    expect(out).toHaveLength(1);
    expect(out[0]!.output).toBe("Downloading 100%");
  });

  it("finalises on the closing frame with the complete output", () => {
    const out = ingest([
      frame({ id: "a", summary: "$ echo hi", state: "running" }),
      frame({ id: "a", delta: "h", state: "running" }),
      frame({ id: "a", summary: "$ echo hi", output: "hi\n", state: "done" }),
    ]);

    expect(out[0]!.state).toBe("done");
    // The closing frame is authoritative: a client that opened the panel
    // mid-command gets the whole output, not the deltas it happened to catch.
    expect(out[0]!.output).toBe("hi\n");
  });

  it("does not blank an entry when the closing frame carries no output", () => {
    const out = ingest([
      frame({ id: "a", summary: "$ tail -f log", state: "running" }),
      frame({ id: "a", delta: "line one\n", state: "running" }),
      frame({ id: "a", summary: "[main] killed", state: "done" }),
    ]);
    expect(out[0]!.output).toBe("line one\n");
  });

  it("keeps two concurrent commands apart", () => {
    const out = ingest([
      frame({ id: "a", summary: "$ one", state: "running" }),
      frame({ id: "b", summary: "$ two", state: "running" }),
      frame({ id: "a", delta: "A", state: "running" }),
      frame({ id: "b", delta: "B", state: "running" }),
    ]);

    expect(out).toHaveLength(2);
    expect(out.find((e) => e.id === "a")!.output).toBe("A");
    expect(out.find((e) => e.id === "b")!.output).toBe("B");
  });

  it("gives every entry a stable uid that survives later frames", () => {
    const first = ingest([
      frame({ id: "a", summary: "$ x", state: "running" }),
    ]);
    const uid = first[0]!.uid;
    expect(uid).toBeTruthy();

    const later = ingest(
      [frame({ id: "a", delta: "out", state: "running" })],
      first,
    );
    expect(later[0]!.uid).toBe(uid);
  });

  it("never leaks the wire-only delta/replace fields into the entry", () => {
    const out = ingest([
      frame({ id: "a", summary: "$ x", state: "running", delta: "seed" }),
    ]);
    expect(out[0]!.delta).toBeUndefined();
    expect(out[0]!.replace).toBeUndefined();
    expect(out[0]!.output).toBe("seed");
  });
});

describe("the display window", () => {
  it("keeps the command line visible after hundreds of deltas", () => {
    // The regression this exists for: `npm install` emits far more frames than
    // the window holds, and the `$ npm install` row must not be evicted by its
    // own output.
    const frames = [
      frame({ id: "a", summary: "$ npm install", state: "running" }),
      ...Array.from({ length: 500 }, (_, i) =>
        frame({ id: "a", delta: `line ${i}\n`, state: "running" }),
      ),
    ];

    let list = ingest(frames);
    // The hook windows *after* folding.
    list =
      list.length > MAX_EVENTS ? list.slice(list.length - MAX_EVENTS) : list;

    expect(list).toHaveLength(1);
    expect(list[0]!.summary).toBe("$ npm install");
    expect(list[0]!.output).toContain("line 499");
  });

  it("still windows unrelated events", () => {
    let list = ingest(
      Array.from({ length: 250 }, (_, i) => frame({ summary: `cmd ${i}` })),
    );
    list =
      list.length > MAX_EVENTS ? list.slice(list.length - MAX_EVENTS) : list;

    expect(list).toHaveLength(MAX_EVENTS);
    expect(list[list.length - 1]!.summary).toBe("cmd 249");
  });
});
