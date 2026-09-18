"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { type Credits, isCredits } from "@/core/credits";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

export function CreditsMeter() {
  const { t } = useI18n();
  const [credits, setCredits] = useState<Credits | null>(null);
  const [error, setError] = useState(false);
  const [reason, setReason] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const load = () =>
    fetch("/api/v1/credits")
      .then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(String(r.status))),
      )
      .then((data: unknown) => {
        if (!isCredits(data)) {
          throw new Error("malformed credits payload");
        }
        setCredits(data);
        setStatus(data.request_status);
      })
      .catch(() => setError(true));

  useEffect(() => {
    void load();
  }, []);

  if (error) {
    return (
      <SettingsSection
        title={t.settings.account.creditsTitle}
        description={t.settings.account.creditsDescription}
      >
        <p className="text-muted-foreground text-sm">
          Credits information unavailable.
        </p>
      </SettingsSection>
    );
  }

  if (!credits) return null;

  const usedPct =
    credits.daily_limit > 0
      ? Math.min(100, Math.round((credits.used / credits.daily_limit) * 100))
      : 0;
  const low =
    !credits.unlimited && credits.remaining <= credits.daily_limit * 0.1;

  const sendRequest = async () => {
    setSending(true);
    try {
      const res = await fetch("/api/v1/credits/request", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
        body: JSON.stringify({ reason: reason.trim() || null }),
      });
      if (!res.ok) {
        toast.error(t.settings.account.billingActionFailed);
        return;
      }
      setStatus("pending");
      setShowForm(false);
      setReason("");
    } catch {
      toast.error(t.settings.account.billingActionFailed);
      // Request failed — user can retry
    } finally {
      setSending(false);
    }
  };

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
        <div className="max-w-sm space-y-3">
          {/* Usage-first: what you've used out of your limit today. */}
          <div className="flex items-baseline justify-between text-sm">
            <span className="text-foreground font-medium tabular-nums">
              {credits.used.toLocaleString()} /{" "}
              {credits.daily_limit.toLocaleString()}
            </span>
            <span className="text-muted-foreground text-xs">
              {t.settings.account.creditsUsedToday}
            </span>
          </div>
          <div
            className="bg-muted h-2 overflow-hidden rounded-full"
            role="progressbar"
            aria-label={t.settings.account.creditsUsedToday}
            aria-valuenow={Math.round(usedPct)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuetext={`${credits.used.toLocaleString()} / ${credits.daily_limit.toLocaleString()}`}
          >
            <div
              className={
                low
                  ? "bg-destructive h-full rounded-full transition-all"
                  : "bg-brand-gradient h-full rounded-full transition-all"
              }
              style={{ width: `${usedPct}%` }}
            />
          </div>
          <p className="text-muted-foreground text-xs">
            {credits.remaining.toLocaleString()} left ·{" "}
            {t.settings.account.creditsResets}
          </p>

          {/* Self-service request path (hybrid wall). */}
          {status === "pending" ? (
            <p className="text-warning dark:text-warning text-xs">
              {t.settings.account.creditsRequestPending}
            </p>
          ) : showForm ? (
            <div className="space-y-2">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={t.settings.account.creditsRequestReason}
                rows={2}
                className="border-input bg-background w-full rounded-md border px-2 py-1.5 text-sm"
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={sendRequest} disabled={sending}>
                  {t.settings.account.creditsRequestSend}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowForm(false)}
                >
                  ×
                </Button>
              </div>
            </div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowForm(true)}
            >
              {t.settings.account.creditsRequestMore}
            </Button>
          )}
        </div>
      )}
    </SettingsSection>
  );
}
