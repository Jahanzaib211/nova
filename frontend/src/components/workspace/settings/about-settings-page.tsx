"use client";

import { ClipboardSafeStreamdown } from "@/components/ai-elements/streamdown";
import { useFeatureFlags } from "@/core/runtime/feature-flags";
import { UpdatesPage } from "@/features/console/pages/updates-page";

import { aboutMarkdown } from "./about-content";

/** About & versions: the static about text, then what version of each moving
 * part this deployment runs (formerly a separate "Updates" page). */
export function AboutSettingsPage() {
  const flags = useFeatureFlags();
  return (
    <div className="space-y-8">
      <ClipboardSafeStreamdown>{aboutMarkdown}</ClipboardSafeStreamdown>
      {flags.capabilities && <UpdatesPage />}
    </div>
  );
}
