"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/core/runtime/feature-flags";
import { settingsGroups, settingsPages } from "@/features/registry";
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
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");

  const visible = useMemo(
    () =>
      settingsPages().filter(
        (page) =>
          (page.flag === undefined || flags[page.flag]) &&
          (!page.adminOnly || isAdmin),
      ),
    [flags, isAdmin],
  );

  const sections = useMemo(
    () =>
      visible.map((page) => ({
        id: page.id,
        label: page.label(t),
        icon: page.icon,
        Page: page.Page,
        group: page.group ?? "general",
      })),
    [t, visible],
  );

  const groups = useMemo(
    () =>
      settingsGroups(visible).map(({ group, pages }) => ({
        group,
        label: t.settings.groups[group],
        ids: pages.map((p) => p.id),
      })),
    [t, visible],
  );

  return { sections, groups, activeSection, setActiveSection };
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
  const { sections, groups } = useSettingsSections();
  const active = sections.find((section) => section.id === activeSection);
  const byId = new Map(sections.map((s) => [s.id, s] as const));

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] gap-4 md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1">
      <nav
        className="bg-sidebar min-h-0 overflow-x-auto rounded-lg border p-2 md:overflow-x-visible md:overflow-y-auto"
        data-testid="settings-rail"
      >
        <ul className="flex gap-1 md:block md:space-y-1 md:pr-1">
          {groups.map(({ group, label: groupLabel, ids }) => (
            <li key={group} className="contents md:block">
              {/* Group headings read only on the vertical (md+) rail; the
                  horizontal mobile strip stays flat. */}
              <div
                className="text-muted-foreground hidden px-3 pt-3 pb-1 text-[11px] font-semibold tracking-wider uppercase first:pt-1 md:block"
                data-testid={`settings-group-${group}`}
              >
                {groupLabel}
              </div>
              <ul className="contents md:block md:space-y-1">
                {ids.map((id) => {
                  const section = byId.get(id);
                  if (!section) return null;
                  const Icon = section.icon;
                  const isActive = activeSection === id;
                  return (
                    <li key={id} className="shrink-0 md:shrink">
                      <button
                        type="button"
                        onClick={() => onNavigate(id)}
                        className={cn(
                          "flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors md:min-h-0",
                          isActive
                            ? "bg-primary text-primary-foreground shadow-sm"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        <Icon className="size-4" />
                        <span>{section.label}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
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
