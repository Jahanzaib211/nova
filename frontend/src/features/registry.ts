import { integrationsManifest } from "./integrations/manifest";
import { jobsManifest } from "./jobs/manifest";
import { settingsCoreManifest } from "./settings-core/manifest";
import {
  SETTINGS_PAGE_IDS,
  type FeatureManifest,
  type NavSpec,
  type SettingsPageId,
  type SettingsPageSpec,
} from "./types";

/**
 * Every feature module, in one list. Adding a feature = one import + one
 * entry here; the settings dialog, the /workspace/settings route and future
 * nav consumers read from it.
 */
export const FEATURE_MANIFESTS: readonly FeatureManifest[] = [
  settingsCoreManifest,
  jobsManifest,
  integrationsManifest,
];

/** All registered sidebar entries, in order. */
export function navItems(): NavSpec[] {
  return FEATURE_MANIFESTS.flatMap((m) => m.nav ?? []).sort(
    (a, b) => a.order - b.order,
  );
}

/** All registered settings pages, in rail order. */
export function settingsPages(): SettingsPageSpec[] {
  return FEATURE_MANIFESTS.flatMap((m) => m.settings ?? []).sort(
    (a, b) => a.order - b.order,
  );
}

export function isSettingsPageId(value: string): value is SettingsPageId {
  return (SETTINGS_PAGE_IDS as readonly string[]).includes(value);
}
