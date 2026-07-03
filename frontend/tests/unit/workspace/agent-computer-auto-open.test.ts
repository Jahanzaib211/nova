import { describe, expect, test } from "vitest";

import { AgentComputerAutoOpenPolicy } from "@/components/workspace/panels/auto-open-policy";

describe("AgentComputerAutoOpenPolicy", () => {
  test("does not open before any activity; opens once on first activity", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
    expect(p.shouldAutoOpen()).toBe(false);
  });

  test("a thinking-only run (no activity) never asks to open", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onRunStateChange(true);
    p.onRunStateChange(false);
    // No shouldAutoOpen calls during the run — nothing opened, no state leaks.
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
  });

  test("user closing mid-run suppresses reopen for that run", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
    // user closes the panel while the run is still active
    p.onPanelOpenChange(false, true);
    expect(p.shouldAutoOpen()).toBe(false);
    expect(p.shouldAutoOpen()).toBe(false);
  });

  test("next run re-arms auto-open after a mid-run close", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
    p.onPanelOpenChange(false, true);
    p.onRunStateChange(false);
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
  });

  test("closing the panel while idle does not affect the next run", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onPanelOpenChange(false, true); // closed while no run active
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
  });

  test("loading staying true across renders does not re-arm", () => {
    const p = new AgentComputerAutoOpenPolicy();
    p.onRunStateChange(true);
    expect(p.shouldAutoOpen()).toBe(true);
    p.onPanelOpenChange(false, true);
    p.onRunStateChange(true); // same run, repeated render
    expect(p.shouldAutoOpen()).toBe(false);
  });
});
