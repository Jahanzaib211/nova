"use client";

import { useCallback, useEffect, useRef } from "react";

import { AgentComputerAutoOpenPolicy } from "./auto-open-policy";
import { usePanels } from "./context";

/**
 * Auto-open wiring for the Agent's Computer panel.
 *
 * Call `notifyComputerActivity()` from tool-activity stream callbacks and
 * feed `syncRunState(thread.isLoading)` from an effect. The panel opens the
 * first time the agent actually uses its computer in a run; see
 * AgentComputerAutoOpenPolicy for the exact rules.
 *
 * The policy lives at MODULE scope, not per-mount: previously a ref died with
 * the component, so "close the panel mid-run" was forgotten by simply
 * navigating away and back during the same run — the next tool event then
 * re-opened the panel against an explicit user decision. A module singleton
 * survives route changes; it resets on the run boundary (`onRunStateChange`),
 * which is the only reset that should exist.
 */
const sharedPolicy = new AgentComputerAutoOpenPolicy();

export function useAutoOpenAgentComputer() {
  const { agentComputerOpen, setAgentComputerOpen } = usePanels();
  // Per-mount "previous" is enough to report transitions: on a fresh mount it
  // equals the current state, so no phantom transition is reported, while the
  // accumulated userClosed/autoOpened state itself lives in sharedPolicy.
  const prevOpenRef = useRef(agentComputerOpen);

  useEffect(() => {
    sharedPolicy.onPanelOpenChange(agentComputerOpen, prevOpenRef.current);
    prevOpenRef.current = agentComputerOpen;
  }, [agentComputerOpen]);

  const syncRunState = useCallback((isLoading: boolean) => {
    sharedPolicy.onRunStateChange(isLoading);
  }, []);

  const notifyComputerActivity = useCallback(() => {
    if (sharedPolicy.shouldAutoOpen()) {
      setAgentComputerOpen(true);
    }
  }, [setAgentComputerOpen]);

  return { notifyComputerActivity, syncRunState };
}
