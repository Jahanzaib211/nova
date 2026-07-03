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
 */
export function useAutoOpenAgentComputer() {
  const { agentComputerOpen, setAgentComputerOpen } = usePanels();
  const policyRef = useRef<AgentComputerAutoOpenPolicy | null>(null);
  policyRef.current ??= new AgentComputerAutoOpenPolicy();
  const prevOpenRef = useRef(agentComputerOpen);

  useEffect(() => {
    policyRef.current?.onPanelOpenChange(
      agentComputerOpen,
      prevOpenRef.current,
    );
    prevOpenRef.current = agentComputerOpen;
  }, [agentComputerOpen]);

  const syncRunState = useCallback((isLoading: boolean) => {
    policyRef.current?.onRunStateChange(isLoading);
  }, []);

  const notifyComputerActivity = useCallback(() => {
    if (policyRef.current?.shouldAutoOpen()) {
      setAgentComputerOpen(true);
    }
  }, [setAgentComputerOpen]);

  return { notifyComputerActivity, syncRunState };
}
