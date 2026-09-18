import { settingsCoreManifest } from "./settings-core/manifest";
import {
  SETTINGS_PAGE_IDS,
  type FeatureManifest,
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
];

/** All registered settings pages, in rail order. */
export function settingsPages(): SettingsPageSpec[] {
  return FEATURE_MANIFESTS.flatMap((m) => m.settings ?? []).sort(
    (a, b) => a.order - b.order,
  );
}

export function isSettingsPageId(value: string): value is SettingsPageId {
  return (SETTINGS_PAGE_IDS as readonly string[]).includes(value);
}
