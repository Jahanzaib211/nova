import { describe, expect, test } from "vitest";

import { formatContextWindow, getModelLabel } from "@/core/models/types";

describe("getModelLabel", () => {
  test("prefers display_name when present", () => {
    expect(getModelLabel({ name: "m1", display_name: "MiniMax M3" })).toBe(
      "MiniMax M3",
    );
  });

  test("falls back to name when display_name is null (runtime local models)", () => {
    expect(getModelLabel({ name: "local-llm", display_name: null })).toBe(
      "local-llm",
    );
  });

  test("falls back to name when display_name is empty or whitespace", () => {
    expect(getModelLabel({ name: "local-llm", display_name: "" })).toBe(
      "local-llm",
    );
    expect(getModelLabel({ name: "local-llm", display_name: "  " })).toBe(
      "local-llm",
    );
  });

  test("trims display_name", () => {
    expect(getModelLabel({ name: "m", display_name: " GPT " })).toBe("GPT");
  });
});

describe("formatContextWindow", () => {
  test("renders millions and thousands compactly", () => {
    expect(formatContextWindow(1_000_000)).toBe("1M");
    expect(formatContextWindow(1_500_000)).toBe("1.5M");
    expect(formatContextWindow(204_800)).toBe("205K");
    expect(formatContextWindow(128_000)).toBe("128K");
  });

  test("leaves small windows as a plain number", () => {
    expect(formatContextWindow(512)).toBe("512");
  });

  test("returns null when no window was declared", () => {
    expect(formatContextWindow(undefined)).toBeNull();
    expect(formatContextWindow(null)).toBeNull();
    expect(formatContextWindow(0)).toBeNull();
    expect(formatContextWindow(Number.NaN)).toBeNull();
  });
});
