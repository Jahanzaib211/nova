"use client";

import { useEffect, useState } from "react";

import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  SettingsSectionsShell,
  type SettingsSection,
} from "@/components/workspace/settings/settings-dialog";
import { useI18n } from "@/core/i18n/hooks";
import { isSettingsPageId } from "@/features/registry";

/**
 * Standalone settings page. The same section rail + content used by the
 * settings modal, promoted to a routable surface so error toasts and docs
 * can point operators and users at /settings instead of "open the settings
 * dialog".
 */
export default function SettingsPage() {
  const { t } = useI18n();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");

  // Anchor support: /settings#memory opens the memory section directly.
  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace("#", "");
      // An unknown hash used to select a section that does not exist and
      // render an empty body; ignore it and keep the current section.
      if (isSettingsPageId(hash)) setActiveSection(hash);
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-2 border-b px-4 py-4 sm:px-6">
        {/* The sidebar collapses into a sheet below md; without a trigger this
            route was a dead end on phones (no way back to the chat list). */}
        <SidebarTrigger className="mt-0.5 md:hidden" />
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight">
            {t.settings.title}
          </h1>
          <p className="text-muted-foreground text-sm">
            {t.settings.description}
          </p>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 p-4 sm:p-6">
        <SettingsSectionsShell
          activeSection={activeSection}
          onNavigate={setActiveSection}
        />
      </div>
    </div>
  );
}
