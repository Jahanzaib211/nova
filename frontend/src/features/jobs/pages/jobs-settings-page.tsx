"use client";

import { JobsList } from "../components/jobs-list";
import { SchedulesPanel } from "../components/schedules-panel";

export function JobsSettingsPage() {
  return (
    <div className="space-y-8">
      <JobsList />
      <SchedulesPanel />
    </div>
  );
}
