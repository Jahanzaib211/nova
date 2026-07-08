"use client";

import { BotIcon, PlusIcon, PowerOffIcon, TriangleAlertIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useAgents } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

import { AgentCard } from "./agent-card";

export function AgentGallery() {
  const { t } = useI18n();
  const { agents, isLoading, error, isDisabled } = useAgents();
  const router = useRouter();

  const handleNewAgent = () => {
    router.push("/workspace/agents/new");
  };

  const renderContent = () => {
    if (isLoading) {
      return (
        <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
          {t.common.loading}
        </div>
      );
    }

    // Agent-management API is disabled server-side: tell the truth instead of
    // inviting the user into a creation flow that would 403 on submit.
    if (isDisabled) {
      return (
        <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
          <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
            <PowerOffIcon className="text-muted-foreground h-7 w-7" />
          </div>
          <div>
            <p className="font-medium">{t.agents.apiDisabledTitle}</p>
            <p className="text-muted-foreground mx-auto mt-1 max-w-md text-sm">
              {t.agents.apiDisabledDescription}
            </p>
          </div>
        </div>
      );
    }

    if (error) {
      return (
        <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
          <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
            <TriangleAlertIcon className="text-muted-foreground h-7 w-7" />
          </div>
          <div>
            <p className="font-medium">{t.agents.loadErrorTitle}</p>
            <p className="text-muted-foreground mx-auto mt-1 max-w-md text-sm">
              {t.agents.loadErrorDescription}
            </p>
          </div>
          <Button
            variant="outline"
            className="mt-2"
            onClick={() => router.refresh()}
          >
            {t.agents.retry}
          </Button>
        </div>
      );
    }

    if (agents.length === 0) {
      return (
        <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
          <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
            <BotIcon className="text-muted-foreground h-7 w-7" />
          </div>
          <div>
            <p className="font-medium">{t.agents.emptyTitle}</p>
            <p className="text-muted-foreground mt-1 text-sm">
              {t.agents.emptyDescription}
            </p>
          </div>
          <Button variant="outline" className="mt-2" onClick={handleNewAgent}>
            <PlusIcon className="mr-1.5 h-4 w-4" />
            {t.agents.newAgent}
          </Button>
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {agents.map((agent) => (
          <AgentCard key={agent.name} agent={agent} />
        ))}
      </div>
    );
  };

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.agents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.agents.description}
          </p>
        </div>
        <Button onClick={handleNewAgent} disabled={isDisabled}>
          <PlusIcon className="mr-1.5 h-4 w-4" />
          {t.agents.newAgent}
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">{renderContent()}</div>
    </div>
  );
}
