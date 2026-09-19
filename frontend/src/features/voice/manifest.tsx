import { MicIcon } from "lucide-react";

import type { FeatureManifest } from "../types";

import { VoiceSettingsPage } from "./pages/voice-settings-page";

export const voiceManifest: FeatureManifest = {
  id: "voice",
  settings: [
    {
      id: "voice",
      order: 80,
      icon: MicIcon,
      label: (t) => t.settings.sections.voice,
      Page: VoiceSettingsPage,
    },
  ],
};
