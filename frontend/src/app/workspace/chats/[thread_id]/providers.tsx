"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { PanelsProvider } from "@/components/workspace/panels/context";
import { SubtasksProvider } from "@/core/tasks/context";

export function ChatProviders({ children }: { children: React.ReactNode }) {
  return (
    <PanelsProvider>
      <SubtasksProvider>
        <ArtifactsProvider>
          <PromptInputProvider>{children}</PromptInputProvider>
        </ArtifactsProvider>
      </SubtasksProvider>
    </PanelsProvider>
  );
}
