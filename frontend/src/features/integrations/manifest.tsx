import { PlugZapIcon } from "lucide-react";

import type { FeatureManifest } from "../types";

import { IntegrationsSettingsPage } from "./pages/integrations-settings-page";

export const integrationsManifest: FeatureManifest = {
  id: "integrations",
  settings: [
    {
      id: "integrations",
      order: 87,
      group: "connections",
      icon: PlugZapIcon,
      label: (t) => t.settings.sections.integrations,
      Page: IntegrationsSettingsPage,
      flag: "integrations",
    },
  ],
};
