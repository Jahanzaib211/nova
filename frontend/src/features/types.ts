import type { LucideIcon } from "lucide-react";

import type { Translations } from "@/core/i18n/locales/types";

/**
 * Every settings page id. The union stays closed so deep links
 * (`/workspace/settings#voice`, the nav menu's `defaultSection`) are
 * type-checked; a new page adds its id here and its spec in a manifest.
 */
export const SETTINGS_PAGE_IDS = [
  "account",
  "appearance",
  "memory",
  "runtime",
  "tools",
  "skills",
  "notification",
  "voice",
  "jobs",
  "channels",
  "models",
  "about",
] as const;
export type SettingsPageId = (typeof SETTINGS_PAGE_IDS)[number];

/** Server-declared feature switches (see useFeatureFlags). */
export type FeatureFlagKey =
  | "jobs"
  | "integrations"
  | "email_marketing"
  | "acp_agents";

export interface SettingsPageSpec {
  id: SettingsPageId;
  /** Ascending; the rail renders in this order. */
  order: number;
  icon: LucideIcon;
  label: (t: Translations) => string;
  Page: React.ComponentType<{ onClose?: () => void }>;
  /** When set, the page is listed only while the flag is on. */
  flag?: FeatureFlagKey;
}

export interface NavSpec {
  id: string;
  /** Ascending; rendered after the built-in Chats / Agents entries. */
  order: number;
  icon: LucideIcon;
  label: (t: Translations) => string;
  href: string;
  flag?: FeatureFlagKey;
}

export interface FeatureManifest {
  id: string;
  settings?: SettingsPageSpec[];
  /** Sidebar entries this feature contributes. */
  nav?: NavSpec[];
}
