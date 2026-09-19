import { MailIcon } from "lucide-react";

import type { FeatureManifest } from "../types";

import { EmailSettingsPage } from "./pages/email-settings-page";

export const emailMarketingManifest: FeatureManifest = {
  id: "email-marketing",
  settings: [
    {
      id: "email",
      order: 88,
      icon: MailIcon,
      label: (t) => t.settings.sections.email,
      Page: EmailSettingsPage,
      flag: "email_marketing",
    },
  ],
  nav: [
    {
      id: "email",
      order: 40,
      icon: MailIcon,
      label: (t) => t.sidebar.email,
      href: "/workspace/email",
      flag: "email_marketing",
    },
  ],
};
