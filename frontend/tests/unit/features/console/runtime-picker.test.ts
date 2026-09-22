import { describe, expect, it } from "vitest";

import {
  isAcpRuntime,
  runtimeTriggerLabel,
  type RuntimeOption,
} from "@/features/console/components/runtime-picker";

/**
 * The model button must say what actually answers the chat. An ACP runtime
 * (Claude Code, OpenClaw) brings its own model, so the button names the
 * runtime and the account — never Nova's model list, which does not apply.
 */
const runtimes: RuntimeOption[] = [
  {
    id: "native",
    label: "Nova (native)",
    kind: "native",
    binary_on_path: true,
    accounts: [
      { id: "configured-models", label: "Configured models", available: true },
    ],
  },
  {
    id: "claude_code",
    label: "Claude Code",
    kind: "acp",
    binary_on_path: true,
    accounts: [
      { id: "claude-login", label: "Claude login", available: true },
      { id: "anthropic-api-key", label: "Anthropic API key", available: false },
    ],
  },
];

describe("runtime picker", () => {
  it("names the runtime and the resolved account on the trigger for ACP runtimes", () => {
    expect(
      runtimeTriggerLabel(
        "claude_code",
        runtimes,
        { runtime: "claude_code" },
        "Automatic",
      ),
    ).toEqual({
      title: "Claude Code",
      subtitle: "Claude login",
    });
    expect(
      runtimeTriggerLabel(
        "claude_code",
        runtimes,
        { runtime: "claude_code", runtime_account: "anthropic-api-key" },
        "Automatic",
      ),
    ).toEqual({ title: "Claude Code", subtitle: "Anthropic API key" });
  });

  it("leaves the model label alone for the native runtime or an unknown one", () => {
    expect(runtimeTriggerLabel("native", runtimes, {}, "Automatic")).toBeNull();
    expect(
      runtimeTriggerLabel(undefined, runtimes, {}, "Automatic"),
    ).toBeNull();
    expect(
      runtimeTriggerLabel("nope", runtimes, { runtime: "nope" }, "Automatic"),
    ).toBeNull();
  });

  it("isAcpRuntime is the rule the model list hides behind", () => {
    expect(isAcpRuntime("claude_code", runtimes)).toBe(true);
    expect(isAcpRuntime("native", runtimes)).toBe(false);
    expect(isAcpRuntime(undefined, runtimes)).toBe(false);
  });
});
