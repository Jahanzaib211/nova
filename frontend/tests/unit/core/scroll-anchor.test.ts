import { describe, expect, test } from "vitest";

import {
  applyScrollAnchor,
  findScrollableAncestor,
  type ScrollNodeLike,
} from "@/core/dom/scroll-anchor";

/** Minimal stand-in for an element chain; the frontend has no jsdom. */
function node(
  overflow: string,
  { scrollHeight = 0, clientHeight = 0 } = {},
): ScrollNodeLike & { overflow: string } {
  return {
    overflow,
    parentElement: null,
    scrollTop: 0,
    scrollHeight,
    clientHeight,
  };
}

const overflowOf = (n: ScrollNodeLike) =>
  (n as { overflow?: string }).overflow ?? "visible";

function chain(...nodes: ScrollNodeLike[]) {
  nodes.forEach((n, i) => {
    n.parentElement = nodes[i + 1] ?? null;
  });
  return nodes[0]!;
}

describe("findScrollableAncestor", () => {
  test("walks past non-scrolling ancestors to the real scroller", () => {
    const scroller = node("auto", { scrollHeight: 900, clientHeight: 300 });
    const leaf = chain(node("visible"), node("hidden"), scroller);
    expect(findScrollableAncestor(leaf, overflowOf)).toBe(scroller);
  });

  test("ignores an overflow-auto element whose content fits", () => {
    // The trap this guards: adjusting a container that does not scroll is a
    // silent no-op, and the jump stays.
    const fits = node("auto", { scrollHeight: 300, clientHeight: 300 });
    const real = node("scroll", { scrollHeight: 900, clientHeight: 300 });
    const leaf = chain(node("visible"), fits, real);
    expect(findScrollableAncestor(leaf, overflowOf)).toBe(real);
  });

  test("returns null when nothing scrolls", () => {
    const leaf = chain(node("visible"), node("hidden"));
    expect(findScrollableAncestor(leaf, overflowOf)).toBeNull();
  });
});

describe("applyScrollAnchor", () => {
  function setup() {
    const scroller = node("auto", { scrollHeight: 2000, clientHeight: 400 });
    scroller.scrollTop = 500;
    const trigger = node("visible");
    chain(trigger, node("visible"), scroller);
    return { scroller, trigger };
  }

  test("pushes the scroller by however far the trigger moved", () => {
    const { scroller, trigger } = setup();
    // Trigger was at y=120 and expanding pushed it down to y=260.
    expect(applyScrollAnchor(trigger, 120, 260, overflowOf)).toBe(true);
    expect(scroller.scrollTop).toBe(640);
  });

  test("compensates upward movement too", () => {
    const { scroller, trigger } = setup();
    expect(applyScrollAnchor(trigger, 260, 120, overflowOf)).toBe(true);
    expect(scroller.scrollTop).toBe(360);
  });

  test("ignores sub-pixel drift", () => {
    const { scroller, trigger } = setup();
    expect(applyScrollAnchor(trigger, 120, 120.4, overflowOf)).toBe(false);
    expect(scroller.scrollTop).toBe(500);
  });

  test("does nothing when there is no scroll container", () => {
    const trigger = chain(node("visible"), node("visible"));
    expect(applyScrollAnchor(trigger, 120, 260, overflowOf)).toBe(false);
  });

  test("tolerates a non-finite measurement", () => {
    const { scroller, trigger } = setup();
    expect(applyScrollAnchor(trigger, NaN, 260, overflowOf)).toBe(false);
    expect(scroller.scrollTop).toBe(500);
  });
});
