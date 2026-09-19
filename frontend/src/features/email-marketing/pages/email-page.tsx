"use client";

import { useEffect, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/core/i18n/hooks";

import { CampaignsPanel } from "../components/campaigns-panel";
import { ContactsPanel } from "../components/contacts-panel";
import { ListsPanel } from "../components/lists-panel";
import { SuppressionsPanel } from "../components/suppressions-panel";
import { TemplatesPanel } from "../components/templates-panel";

export const EMAIL_TABS = [
  "campaigns",
  "lists",
  "contacts",
  "templates",
  "suppressions",
] as const;
export type EmailTab = (typeof EMAIL_TABS)[number];

export function tabFromHash(hash: string): EmailTab {
  const h = hash.replace(/^#/, "");
  return (EMAIL_TABS as readonly string[]).includes(h)
    ? (h as EmailTab)
    : "campaigns";
}

export function EmailPage() {
  const { t } = useI18n();
  const s = t.features.email;
  const [tab, setTab] = useState<EmailTab>("campaigns");
  useEffect(() => {
    setTab(tabFromHash(window.location.hash));
  }, []);
  return (
    <div
      className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-4 sm:p-6"
      data-testid="email-page"
    >
      <header className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{s.title}</h1>
        <p className="text-muted-foreground max-w-prose text-sm">
          {s.description}
        </p>
      </header>
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as EmailTab);
          window.history.replaceState(null, "", `#${v}`);
        }}
      >
        <TabsList className="flex w-full flex-wrap justify-start">
          {EMAIL_TABS.map((id) => (
            <TabsTrigger key={id} value={id}>
              {s.tabs[id]}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="campaigns">
          <CampaignsPanel />
        </TabsContent>
        <TabsContent value="lists">
          <ListsPanel />
        </TabsContent>
        <TabsContent value="contacts">
          <ContactsPanel />
        </TabsContent>
        <TabsContent value="templates">
          <TemplatesPanel />
        </TabsContent>
        <TabsContent value="suppressions">
          <SuppressionsPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
