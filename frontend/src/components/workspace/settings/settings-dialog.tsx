"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/core/runtime/feature-flags";
import { settingsPages } from "@/features/registry";
import type { SettingsPageId } from "@/features/types";
import { cn } from "@/lib/utils";

/** Kept as the public name; the ids now live in src/features/types.ts. */
export type SettingsSection = SettingsPageId;

/**
 * Navigation model + body renderer shared by the SettingsDialog modal and
 * the standalone /settings page. The list comes from the feature registry
 * (src/features/registry.ts): a page is declared once, in its feature's
 * manifest, and both surfaces render it — they can never drift apart, and
 * adding a page no longer means editing this file.
 */
export function useSettingsSections() {
  const { t } = useI18n();
  const flags = useFeatureFlags();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");

  const sections = useMemo(
    () =>
      settingsPages()
        .filter((page) => page.flag === undefined || flags[page.flag])
        .map((page) => ({
          id: page.id,
          label: page.label(t),
          icon: page.icon,
          Page: page.Page,
        })),
    [t, flags],
  );

  return { sections, activeSection, setActiveSection };
}

export function SettingsSectionsShell({
  activeSection,
  onNavigate,
  onClose,
}: {
  activeSection: SettingsSection;
  onNavigate: (section: SettingsSection) => void;
  onClose?: () => void;
}) {
  const { sections } = useSettingsSections();
  const active = sections.find((section) => section.id === activeSection);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] gap-4 md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1">
      <nav className="bg-sidebar min-h-0 overflow-x-auto rounded-lg border p-2 md:overflow-x-visible md:overflow-y-auto">
        <ul className="flex gap-1 md:block md:space-y-1 md:pr-1">
          {sections.map(({ id, label, icon: Icon }) => {
            const active = activeSection === id;
            return (
              <li key={id} className="shrink-0 md:shrink">
                <button
                  type="button"
                  onClick={() => onNavigate(id)}
                  className={cn(
                    "flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors md:min-h-0",
                    active
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" />
                  <span>{label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <ScrollArea className="h-full min-h-0 rounded-lg border [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="space-y-8 p-6">
          {active ? <active.Page onClose={onClose} /> : null}
        </div>
      </ScrollArea>
    </div>
  );
}

type SettingsDialogProps = React.ComponentProps<typeof Dialog> & {
  defaultSection?: SettingsSection;
};

export function SettingsDialog(props: SettingsDialogProps) {
  const { defaultSection = "appearance", ...dialogProps } = props;
  const { t } = useI18n();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(defaultSection);

  useEffect(() => {
    // When opening the dialog, ensure the active section follows the caller's intent.
    // This allows triggers like "About" to open the dialog directly on that page.
    if (dialogProps.open) {
      setActiveSection(defaultSection);
    }
  }, [defaultSection, dialogProps.open]);

  return (
    <Dialog
      {...dialogProps}
      onOpenChange={(open) => props.onOpenChange?.(open)}
    >
      <DialogContent
        className="flex h-[75vh] max-h-[calc(100vh-2rem)] flex-col sm:max-w-5xl md:max-w-6xl"
        aria-describedby={undefined}
      >
        <DialogHeader className="gap-1">
          <DialogTitle>{t.settings.title}</DialogTitle>
          <p className="text-muted-foreground text-sm">
            {t.settings.description}
          </p>
        </DialogHeader>
        <SettingsSectionsShell
          activeSection={activeSection}
          onNavigate={setActiveSection}
          onClose={() => props.onOpenChange?.(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
