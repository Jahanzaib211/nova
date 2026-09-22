import { describe, expect, it } from "vitest";

import {
  CHAT_COLUMN_MIN_WIDTH,
  COMPUTER_PANEL_MAX_WIDTH,
  COMPUTER_PANEL_MIN_WIDTH,
  fitPanelWidth,
} from "@/components/workspace/panels/fit-panel-width";

describe("fitPanelWidth", () => {
  it("keeps the preference when the chat keeps its minimum", () => {
    expect(fitPanelWidth(640, 1600)).toBe(640);
  });

  it("yields to the chat column when the area is tight", () => {
    // 1024px area: 640 would leave the chat 384px. Yield to 1024 - 520.
    expect(fitPanelWidth(640, 1024)).toBe(1024 - CHAT_COLUMN_MIN_WIDTH);
  });

  it("never goes below its own floor even when both cannot fit", () => {
    expect(fitPanelWidth(640, 700)).toBe(COMPUTER_PANEL_MIN_WIDTH);
  });

  it("clamps the stored preference to the allowed range", () => {
    expect(fitPanelWidth(10, null)).toBe(COMPUTER_PANEL_MIN_WIDTH);
    expect(fitPanelWidth(5000, null)).toBe(COMPUTER_PANEL_MAX_WIDTH);
  });

  it("ignores an unknown container width", () => {
    expect(fitPanelWidth(800, null)).toBe(800);
    expect(fitPanelWidth(800, 0)).toBe(800);
  });
});
