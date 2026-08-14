"use client";

import {
  BellIcon,
  MicIcon,
  CableIcon,
  CpuIcon,
  InfoIcon,
  BrainIcon,
  PaletteIcon,
  SparklesIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { AccountSettingsPage } from "@/components/workspace/settings/account-settings-page";
import { AppearanceSettingsPage } from "@/components/workspace/settings/appearance-settings-page";
import { ChannelsSettingsPage } from "@/components/workspace/settings/channels-settings-page";
import { MemorySettingsPage } from "@/components/workspace/settings/memory-settings-page";
import { ModelsSettingsPage } from "@/components/workspace/settings/models-settings-page";
import { NotificationSettingsPage } from "@/components/workspace/settings/notification-settings-page";
import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { ToolSettingsPage } from "@/components/workspace/settings/tool-settings-page";
import { VoiceSettingsPage } from "@/components/workspace/settings/voice-settings-page";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type SettingsSection =
  | "account"
  | "appearance"
  | "channels"
  | "models"
  | "memory"
  | "tools"
  | "skills"
  | "notification"
  | "voice"
  | "about";

/**
 * Navigation model + body renderer shared by the SettingsDialog modal and
 * the standalone /settings page. Keeps the section list in one place so
 * the two surfaces can never drift apart.
 */
export function useSettingsSections() {
  const { t } = useI18n();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");

  const sections = useMemo(
    () => [
      { id: "account", label: t.settings.sections.account, icon: UserIcon },
      {
        id: "appearance",
        label: t.settings.sections.appearance,
        icon: PaletteIcon,
      },
      {
        id: "memory",
        label: t.settings.sections.memory,
        icon: BrainIcon,
      },
      { id: "tools", label: t.settings.sections.tools, icon: WrenchIcon },
      { id: "skills", label: t.settings.sections.skills, icon: SparklesIcon },
      {
        id: "notification",
        label: t.settings.sections.notification,
        icon: BellIcon,
      },
      { id: "voice", label: t.settings.sections.voice, icon: MicIcon },
      {
        id: "channels",
        label: t.settings.sections.channels,
        icon: CableIcon,
      },
      { id: "models", label: t.settings.sections.models, icon: CpuIcon },
      { id: "about", label: t.settings.sections.about, icon: InfoIcon },
    ],
    [t],
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

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-4 md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1">
      <nav className="bg-sidebar min-h-0 overflow-x-auto rounded-lg border p-2 md:overflow-x-visible md:overflow-y-auto">
        <ul className="flex gap-1 md:block md:space-y-1 md:pr-1">
          {sections.map(({ id, label, icon: Icon }) => {
            const active = activeSection === id;
            return (
              <li key={id} className="shrink-0 md:shrink">
                <button
                  type="button"
                  onClick={() => onNavigate(id as SettingsSection)}
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
          {activeSection === "account" && <AccountSettingsPage />}
          {activeSection === "appearance" && <AppearanceSettingsPage />}
          {activeSection === "memory" && <MemorySettingsPage />}
          {activeSection === "tools" && <ToolSettingsPage />}
          {activeSection === "skills" && (
            <SkillSettingsPage onClose={onClose} />
          )}
          {activeSection === "notification" && <NotificationSettingsPage />}
          {activeSection === "voice" && <VoiceSettingsPage />}
          {activeSection === "channels" && <ChannelsSettingsPage />}
          {activeSection === "models" && <ModelsSettingsPage />}
          {activeSection === "about" && <AboutSettingsPage />}
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
