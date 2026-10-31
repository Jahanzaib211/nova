import { describe, expect, it } from "vitest";

import {
  KEYBOARD_STEP,
  KEYBOARD_STEP_LARGE,
  dragWidth,
  keyboardWidth,
  readStoredWidth,
} from "@/components/workspace/panels/use-panel-width";

/**
 * The resize maths behind <SidePanel>. Pointer, keyboard and wheel resizing
 * all reduce to these pure functions so the behaviour is testable without a
 * browser; the hook only wires events to them.
 */
describe("dragWidth", () => {
  it("grows a right-side panel as the pointer moves left", () => {
    expect(
      dragWidth({ side: "right", startWidth: 600, startX: 800, clientX: 700 }),
    ).toBe(700);
    expect(
      dragWidth({ side: "right", startWidth: 600, startX: 800, clientX: 900 }),
    ).toBe(500);
  });

  it("grows a left-side panel as the pointer moves right", () => {
    expect(
      dragWidth({ side: "left", startWidth: 300, startX: 300, clientX: 340 }),
    ).toBe(340);
  });
});

describe("keyboardWidth", () => {
  it("steps with the arrows, larger with shift, and ignores other keys", () => {
    expect(keyboardWidth(600, "right", "ArrowLeft", false)).toBe(
      600 + KEYBOARD_STEP,
    );
    expect(keyboardWidth(600, "right", "ArrowRight", false)).toBe(
      600 - KEYBOARD_STEP,
    );
    expect(keyboardWidth(600, "right", "ArrowLeft", true)).toBe(
      600 + KEYBOARD_STEP_LARGE,
    );
    expect(keyboardWidth(600, "left", "ArrowRight", false)).toBe(
      600 + KEYBOARD_STEP,
    );
    expect(keyboardWidth(600, "left", "Enter", false)).toBeNull();
  });

  it("Home and End jump to the bounds", () => {
    const bounds = { min: 380, max: 1100 };
    expect(keyboardWidth(600, "right", "Home", false, bounds)).toBe(380);
    expect(keyboardWidth(600, "right", "End", false, bounds)).toBe(1100);
  });
});

describe("readStoredWidth", () => {
  it("accepts an in-range stored value and falls back otherwise", () => {
    const opts = { min: 380, max: 1100, fallback: 500 };
    expect(readStoredWidth("640", opts)).toBe(640);
    expect(readStoredWidth("10", opts)).toBe(500);
    expect(readStoredWidth("nope", opts)).toBe(500);
    expect(readStoredWidth(null, opts)).toBe(500);
  });
});
