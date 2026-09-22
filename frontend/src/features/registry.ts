import { consoleManifest } from "./console/manifest";
import { emailMarketingManifest } from "./email-marketing/manifest";
import { integrationsManifest } from "./integrations/manifest";
import { jobsManifest } from "./jobs/manifest";
import { settingsCoreManifest } from "./settings-core/manifest";
import {
  SETTINGS_GROUPS,
  SETTINGS_PAGE_IDS,
  type FeatureManifest,
  type NavSpec,
  type SettingsGroup,
  type SettingsPageId,
  type SettingsPageSpec,
} from "./types";
import { voiceManifest } from "./voice/manifest";

/**
 * Every feature module, in one list. Adding a feature = one import + one
 * entry here; the settings dialog, the /workspace/settings route and future
 * nav consumers read from it.
 */
export const FEATURE_MANIFESTS: readonly FeatureManifest[] = [
  settingsCoreManifest,
  jobsManifest,
  integrationsManifest,
  voiceManifest,
  emailMarketingManifest,
  consoleManifest,
];

/** All registered sidebar entries, in order. */
export function navItems(): NavSpec[] {
  return FEATURE_MANIFESTS.flatMap((m) => m.nav ?? []).sort(
    (a, b) => a.order - b.order,
  );
}

/** All registered settings pages, in rail order (group, then order). */
export function settingsPages(): SettingsPageSpec[] {
  const rank = (g: SettingsGroup | undefined) =>
    SETTINGS_GROUPS.indexOf(g ?? "general");
  return FEATURE_MANIFESTS.flatMap((m) => m.settings ?? []).sort(
    (a, b) => rank(a.group) - rank(b.group) || a.order - b.order,
  );
}

/** Settings pages bucketed by group, empty groups omitted, in rail order. */
export function settingsGroups(
  pages: readonly SettingsPageSpec[] = settingsPages(),
): Array<{ group: SettingsGroup; pages: SettingsPageSpec[] }> {
  return SETTINGS_GROUPS.map((group) => ({
    group,
    pages: pages.filter((p) => (p.group ?? "general") === group),
  })).filter((g) => g.pages.length > 0);
}

export function isSettingsPageId(value: string): value is SettingsPageId {
  return (SETTINGS_PAGE_IDS as readonly string[]).includes(value);
}
