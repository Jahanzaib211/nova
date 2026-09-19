import {
  BellIcon,
  BrainIcon,
  CableIcon,
  CpuIcon,
  InfoIcon,
  PaletteIcon,
  ServerIcon,
  SparklesIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";

import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { AccountSettingsPage } from "@/components/workspace/settings/account-settings-page";
import { AppearanceSettingsPage } from "@/components/workspace/settings/appearance-settings-page";
import { ChannelsSettingsPage } from "@/components/workspace/settings/channels-settings-page";
import { MemorySettingsPage } from "@/components/workspace/settings/memory-settings-page";
import { ModelsSettingsPage } from "@/components/workspace/settings/models-settings-page";
import { NotificationSettingsPage } from "@/components/workspace/settings/notification-settings-page";
import { RuntimeSettingsPage } from "@/components/workspace/settings/runtime-settings-page";
import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { ToolSettingsPage } from "@/components/workspace/settings/tool-settings-page";

import type { FeatureManifest } from "../types";

/**
 * The settings pages that predate the feature registry, declared as one
 * manifest. New features declare their own manifest next to their code and
 * add one line to registry.ts; nothing else needs editing.
 */
export const settingsCoreManifest: FeatureManifest = {
  id: "settings-core",
  settings: [
    {
      id: "account",
      order: 10,
      icon: UserIcon,
      label: (t) => t.settings.sections.account,
      Page: AccountSettingsPage,
    },
    {
      id: "appearance",
      order: 20,
      icon: PaletteIcon,
      label: (t) => t.settings.sections.appearance,
      Page: AppearanceSettingsPage,
    },
    {
      id: "memory",
      order: 30,
      icon: BrainIcon,
      label: (t) => t.settings.sections.memory,
      Page: MemorySettingsPage,
    },
    {
      id: "runtime",
      order: 40,
      icon: ServerIcon,
      label: (t) => t.settings.sections.runtime,
      Page: RuntimeSettingsPage,
    },
    {
      id: "tools",
      order: 50,
      icon: WrenchIcon,
      label: (t) => t.settings.sections.tools,
      Page: ToolSettingsPage,
    },
    {
      id: "skills",
      order: 60,
      icon: SparklesIcon,
      label: (t) => t.settings.sections.skills,
      Page: SkillSettingsPage,
    },
    {
      id: "notification",
      order: 70,
      icon: BellIcon,
      label: (t) => t.settings.sections.notification,
      Page: NotificationSettingsPage,
    },
    {
      id: "channels",
      order: 90,
      icon: CableIcon,
      label: (t) => t.settings.sections.channels,
      Page: ChannelsSettingsPage,
    },
    {
      id: "models",
      order: 100,
      icon: CpuIcon,
      label: (t) => t.settings.sections.models,
      Page: ModelsSettingsPage,
    },
    {
      id: "about",
      order: 110,
      icon: InfoIcon,
      label: (t) => t.settings.sections.about,
      Page: AboutSettingsPage,
    },
  ],
};
