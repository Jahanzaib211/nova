/**
 * Keep a clicked element where the user left it when content above it grows.
 *
 * The chat transcript scrolls inside `use-stick-to-bottom`
 * (`components/ai-elements/conversation.tsx`). Expanding a collapsed drawer --
 * the "Thought for 41 seconds" reasoning panel -- inserts content into the
 * middle of that transcript. Two things then happen, and neither leaves the
 * trigger where the user clicked it:
 *
 * - at the bottom of the transcript, StickToBottom's `resize="smooth"` re-pins
 *   the bottom edge, so the newly revealed text slides up past the viewport;
 * - scrolled up, `scrollTop` does not move while everything below the
 *   insertion point does, so the reader's place shifts by the inserted height.
 *
 * Both read as the panel "jumping" and cutting off the lines above it. The fix
 * is the ordinary accordion contract: measure the trigger before the toggle,
 * and after layout push the scroller by however far it moved.
 *
 * This is deliberately structural rather than typed to `HTMLElement`, and
 * takes the overflow lookup as an argument: the frontend's vitest environment
 * is `node` with no jsdom, so keeping it free of live DOM APIs is what makes
 * it testable at all. `applyScrollAnchor` is the whole behaviour; the call
 * site supplies real elements and `getComputedStyle`.
 */

/** The slice of an element this module actually touches. */
export interface ScrollNodeLike {
  parentElement: ScrollNodeLike | null;
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** Returns the computed `overflow-y` of a node. */
export type OverflowLookup = (node: ScrollNodeLike) => string;

/** `overflow-y` values that make an element a scroll container. */
const SCROLLABLE_OVERFLOW = new Set(["auto", "scroll", "overlay"]);

/**
 * Nearest ancestor that actually scrolls.
 *
 * Both halves matter. An element can declare `overflow-y: auto` and never
 * scroll because its content fits, and adjusting *that* one silently does
 * nothing -- so it must also be overflowing. `start` is included in the walk,
 * so pass the trigger's parent to skip the trigger itself.
 */
export function findScrollableAncestor(
  start: ScrollNodeLike | null,
  overflowOf: OverflowLookup,
): ScrollNodeLike | null {
  for (let node = start; node; node = node.parentElement) {
    if (
      SCROLLABLE_OVERFLOW.has(overflowOf(node)) &&
      node.scrollHeight > node.clientHeight
    ) {
      return node;
    }
  }
  return null;
}

/**
 * Scroll `node`'s container so it sits at `previousTop` again.
 *
 * Returns true when it moved something. `epsilon` swallows sub-pixel drift
 * from layout rounding, which would otherwise fight smooth scrolling with a
 * stream of half-pixel corrections.
 */
export function applyScrollAnchor(
  node: ScrollNodeLike,
  previousTop: number,
  currentTop: number,
  overflowOf: OverflowLookup,
  epsilon = 1,
): boolean {
  const delta = currentTop - previousTop;
  if (!Number.isFinite(delta) || Math.abs(delta) < epsilon) return false;
  const scroller = findScrollableAncestor(node.parentElement, overflowOf);
  if (!scroller) return false;
  scroller.scrollTop += delta;
  return true;
}
