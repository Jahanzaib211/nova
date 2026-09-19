import { ListChecksIcon } from "lucide-react";

import type { FeatureManifest } from "../types";

import { JobsSettingsPage } from "./pages/jobs-settings-page";

export const jobsManifest: FeatureManifest = {
  id: "jobs",
  settings: [
    {
      id: "jobs",
      order: 85,
      icon: ListChecksIcon,
      label: (t) => t.settings.sections.jobs,
      Page: JobsSettingsPage,
      flag: "jobs",
    },
  ],
  nav: [
    {
      id: "jobs",
      order: 30,
      icon: ListChecksIcon,
      label: (t) => t.sidebar.jobs,
      href: "/workspace/jobs",
      flag: "jobs",
    },
  ],
};
