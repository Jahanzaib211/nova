import { expect, test } from "vitest";

import { getEnvCompat } from "@/core/compat/env";

test("reads NOVA_<name> first", () => {
  expect(getEnvCompat("X", { NOVA_X: "new", DEER_FLOW_X: "old" })).toBe("new");
});

test("falls back to the legacy DEER_FLOW_<name>", () => {
  expect(getEnvCompat("X", { DEER_FLOW_X: "old" })).toBe("old");
});

test("returns undefined when neither is set", () => {
  expect(getEnvCompat("X", {})).toBeUndefined();
});
