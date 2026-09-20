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
  "integrations",
  "email",
  "channels",
  "models",
  "about",
  // Console parity (P13): every page is a view over capability operations.
  "gateway",
  "devices",
  "workers",
  "agents",
  "labs",
  "automation",
  "secrets",
  "updates",
] as const;
export type SettingsPageId = (typeof SETTINGS_PAGE_IDS)[number];

/**
 * Settings rail groups, in rail order. A page declares the group it lives
 * under; the rail renders a heading per non-empty group. Mirrors the
 * console layout users already know: general → connections → agents &
 * tools → privacy & security → system.
 */
export const SETTINGS_GROUPS = [
  "general",
  "connections",
  "agents",
  "privacy",
  "system",
] as const;
export type SettingsGroup = (typeof SETTINGS_GROUPS)[number];

/** Server-declared feature switches (see useFeatureFlags). */
export type FeatureFlagKey =
  | "jobs"
  | "integrations"
  | "email_marketing"
  | "acp_agents"
  | "capabilities"
  | "runtimes";

export interface SettingsPageSpec {
  id: SettingsPageId;
  /** Ascending within its group; the rail renders groups in SETTINGS_GROUPS order. */
  order: number;
  /** Rail group. Pages that predate groups default to "general". */
  group?: SettingsGroup;
  /** Listed only for admins (system_role === "admin"). */
  adminOnly?: boolean;
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
