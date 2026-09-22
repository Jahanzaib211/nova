"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { PanelsProvider } from "@/components/workspace/panels/context";
import { SubtasksProvider } from "@/core/tasks/context";

/**
 * The create-agent flow renders the same MessageList as a chat, and
 * MessageList reads the subtask registry. Every other chat route mounts
 * SubtasksProvider in its layout; this one did not, so the page threw
 * "useSubtaskContext must be used within a SubtaskContext.Provider" during
 * SSR and the route boundary showed "Something went wrong" (2026-09-18,
 * caught by the visual baseline). ArtifactsProvider stays in the page,
 * where it already was.
 */
export default function NewAgentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <PanelsProvider>
      <SubtasksProvider>
        <PromptInputProvider>{children}</PromptInputProvider>
      </SubtasksProvider>
    </PanelsProvider>
  );
}
