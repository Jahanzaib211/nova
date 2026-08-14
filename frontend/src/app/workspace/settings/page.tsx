"use client";

import { useEffect, useState } from "react";

import {
  SettingsSectionsShell,
  type SettingsSection,
} from "@/components/workspace/settings/settings-dialog";
import { useI18n } from "@/core/i18n/hooks";

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
      const hash = window.location.hash.replace("#", "") as SettingsSection;
      if (hash) setActiveSection(hash);
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-b px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t.settings.title}
        </h1>
        <p className="text-muted-foreground text-sm">{t.settings.description}</p>
      </header>
      <div className="flex min-h-0 flex-1 p-6">
        <SettingsSectionsShell
          activeSection={activeSection}
          onNavigate={setActiveSection}
        />
      </div>
    </div>
  );
}