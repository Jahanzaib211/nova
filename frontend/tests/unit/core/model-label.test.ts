import { describe, expect, test } from "vitest";

import { getModelLabel } from "@/core/models/types";

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
