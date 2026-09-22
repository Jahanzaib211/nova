"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useI18n } from "@/core/i18n/hooks";

import { SchedulesPanel } from "../components/schedules-panel";

export function JobsSettingsPage() {
  const { t } = useI18n();
  const s = t.features.jobs.settings;
  return (
    <div className="space-y-8">
      <SettingsSection title={s.title} description={s.description}>
        <Button asChild variant="outline" size="sm">
          <Link href="/workspace/jobs">{s.openPage}</Link>
        </Button>
      </SettingsSection>
      <SchedulesPanel />
    </div>
  );
}
