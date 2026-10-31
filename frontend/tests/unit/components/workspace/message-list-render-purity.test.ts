import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * `MessageList` must not write to `SubtasksProvider` while it renders.
 *
 * Subtask state is derived from the message list during the same walk that
 * builds the message JSX, and the derived values were pushed straight into the
 * provider from there. React rejects that:
 *
 *   Cannot update a component (`SubtasksProvider`) while rendering a different
 *   component (`MessageList`)
 *
 * A production build hides the warning, so this only became visible after the
 * frontend moved to `next dev`. The derivation stays where it is; only the
 * write is deferred — queued during render, flushed in an effect.
 *
 * This is a source-shape guard rather than a render test because the failure is
 * a React dev warning, not a thrown error or a wrong DOM: rendering the tree
 * would still pass while the bug was present.
 */
const SOURCE = readFileSync(
  join(process.cwd(), "src/components/workspace/messages/message-list.tsx"),
  "utf8",
);

/** The render body: everything after the queue/flush setup. */
const renderBody = SOURCE.slice(
  SOURCE.indexOf("const results: React.ReactNode[]"),
);

describe("MessageList render purity", () => {
  it("queues subtask updates instead of writing them during render", () => {
    expect(SOURCE).toContain("queueSubtaskUpdate");
    expect(SOURCE).toContain("queuedSubtaskUpdates");
  });

  it("never calls updateSubtask from the JSX-building pass", () => {
    const directCalls = [...SOURCE.matchAll(/(?<![\w.])updateSubtask\s*\(/g)];
    // The only permitted call is the flush inside the effect.
    expect(directCalls).toHaveLength(1);
    const [flushCall] = directCalls;
    const flushIndex = flushCall?.index ?? 0;
    const flushContext = SOURCE.slice(
      Math.max(0, flushIndex - 200),
      flushIndex,
    );
    expect(flushContext).toContain("useEffect");
  });

  it("resets the queue every render so a re-render cannot replay stale writes", () => {
    expect(SOURCE).toMatch(/queuedSubtaskUpdates\.current\s*=\s*\[\]/);
  });

  it("does not reintroduce a direct provider write further down the file", () => {
    expect(renderBody).not.toContain("updateSubtask(");
  });
});
