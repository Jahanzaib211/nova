"use client";

import { createContext, useContext, useState } from "react";

interface PanelsContextType {
  agentComputerOpen: boolean;
  setAgentComputerOpen: (open: boolean) => void;
}

const PanelsContext = createContext<PanelsContextType | undefined>(undefined);

export function PanelsProvider({ children }: { children: React.ReactNode }) {
  const [agentComputerOpen, setAgentComputerOpen] = useState(false);

  return (
    <PanelsContext.Provider value={{ agentComputerOpen, setAgentComputerOpen }}>
      {children}
    </PanelsContext.Provider>
  );
}

const _noop = (): void => undefined;
const _fallback: PanelsContextType = {
  agentComputerOpen: false,
  setAgentComputerOpen: _noop,
};

export function usePanels() {
  return useContext(PanelsContext) ?? _fallback;
}
