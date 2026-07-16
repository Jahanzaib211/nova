"use client";

import { useEffect, useState } from "react";

import { fetch } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

interface Credits {
  plan: string;
  daily_limit: number;
  used: number;
  remaining: number;
  unlimited: boolean;
}

export function CreditsMeter() {
  const { t } = useI18n();
  const [credits, setCredits] = useState<Credits | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/v1/credits", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: Credits) => setCredits(data))
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, []);

  if (error || !credits) return null;

  const pct =
    credits.daily_limit > 0
      ? Math.min(100, Math.round((credits.used / credits.daily_limit) * 100))
      : 0;
  const low = !credits.unlimited && credits.remaining <= credits.daily_limit * 0.1;

  return (
    <SettingsSection
      title={t.settings.account.creditsTitle}
      description={t.settings.account.creditsDescription}
    >
      {credits.unlimited ? (
        <p className="text-foreground text-sm font-medium">
          {t.settings.account.creditsUnlimited}
        </p>
      ) : (
        <div className="max-w-sm space-y-2">
          <div className="flex items-baseline justify-between text-sm">
            <span className="text-foreground font-medium tabular-nums">
              {credits.remaining.toLocaleString()} /{" "}
              {credits.daily_limit.toLocaleString()}
            </span>
            <span className="text-muted-foreground text-xs">
              {t.settings.account.creditsLeftToday}
            </span>
          </div>
          <div className="bg-muted h-2 overflow-hidden rounded-full">
            <div
              className={
                low
                  ? "h-full rounded-full bg-red-500 transition-all"
                  : "h-full rounded-full bg-gradient-to-r from-violet-600 to-cyan-500 transition-all"
              }
              style={{ width: `${100 - pct}%` }}
            />
          </div>
          <p className="text-muted-foreground text-xs">
            {t.settings.account.creditsResets}
          </p>
        </div>
      )}
    </SettingsSection>
  );
}
