import { describe, expect, test } from "vitest";

import { resolveSelectableModel } from "@/core/models/types";

const sonnet = { name: "claude-sonnet-4-6" };
const minimax = { name: "minimax-m3", hidden: true };
const kimi = { name: "fireworks-kimi-k2p7" };

describe("resolveSelectableModel", () => {
  test("keeps a saved choice the menu still offers", () => {
    expect(resolveSelectableModel([sonnet, kimi], "fireworks-kimi-k2p7")).toBe(
      kimi,
    );
  });

  test("drops a saved choice of a hidden model for the first offered one", () => {
    // The live case: MiniMax was hidden after its balance ran out, but a
    // browser that had picked it kept sending it, so every title, suggestion
    // and fallback turn failed with 402.
    expect(resolveSelectableModel([minimax, sonnet, kimi], "minimax-m3")).toBe(
      sonnet,
    );
  });

  test("falls back to the first offered model when nothing is saved", () => {
    expect(resolveSelectableModel([minimax, sonnet], undefined)).toBe(sonnet);
  });

  test("falls back when the saved model no longer exists", () => {
    expect(resolveSelectableModel([sonnet, kimi], "retired-model")).toBe(
      sonnet,
    );
  });

  test("uses a hidden model only when nothing else is offered", () => {
    expect(resolveSelectableModel([minimax], "minimax-m3")).toBe(minimax);
  });

  test("returns undefined for an empty list", () => {
    expect(resolveSelectableModel([], "minimax-m3")).toBeUndefined();
  });

  test("keeps a saved model the caller made available", () => {
    // The user surfaced a hidden model (per-browser visibility / the
    // "show hidden models" toggle); it must stay selected instead of being
    // replaced by the first server-visible entry.
    expect(
      resolveSelectableModel([sonnet, minimax], "minimax-m3", () => true),
    ).toBe(minimax);
  });

  test("honours the caller's availability predicate for the fallback", () => {
    expect(
      resolveSelectableModel(
        [kimi, sonnet],
        "gone",
        (m) => m.name !== "claude-sonnet-4-6",
      ),
    ).toBe(kimi);
  });

  test("still falls back to the first entry when nothing is available", () => {
    expect(
      resolveSelectableModel([minimax, sonnet], "retired", () => false),
    ).toBe(minimax);
  });
});
