import { createContext, useContext } from "react";

/**
 * False while a chat's thread exists only in the browser. A new chat has a
 * client-generated id the gateway has never seen, so every sandbox poll for it
 * 404s and computer-ws is refused, once a second, until the first message
 * creates the thread. The panel still renders (its tabs, empty states and
 * layout are the same either way); only the network legs wait.
 */
const SandboxLiveContext = createContext(true);

export const SandboxLiveProvider = SandboxLiveContext.Provider;

/** `threadId` while the thread exists server-side, otherwise `null`. */
export function useSandboxThreadId(threadId: string | null): string | null {
  return useContext(SandboxLiveContext) ? threadId : null;
}
