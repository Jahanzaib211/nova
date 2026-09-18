"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

interface BillingStatus {
  enabled: boolean;
  plan: string;
  plan_status: string | null;
}

export function BillingSettings() {
  const { t } = useI18n();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/v1/billing", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: BillingStatus | null) => data && setStatus(data))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // Only render when Stripe is configured on this instance.
  if (!status?.enabled) return null;

  const isPaid = status.plan === "plus" || status.plan === "enterprise";

  const go = async (path: "checkout" | "portal") => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/billing/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
        body: "{}",
      });
      if (!res.ok) {
        // Previously this fell through silently: the button re-enabled and
        // nothing else happened, which is indistinguishable from a no-op on
        // the one screen where the user is trying to give you money.
        toast.error(t.settings.account.billingActionFailed);
        return;
      }
      const { url } = (await res.json()) as { url: string };
      window.location.href = url;
    } catch {
      toast.error(t.settings.account.billingActionFailed);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection
      title={t.settings.account.billingTitle}
      description={t.settings.account.billingDescription}
    >
      <div className="max-w-sm space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            {t.settings.account.billingCurrentPlan}
          </span>
          <span className="text-foreground font-semibold capitalize">
            {status.plan}
            {status.plan_status && status.plan_status !== "active"
              ? ` · ${status.plan_status}`
              : ""}
          </span>
        </div>

        {isPaid ? (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => go("portal")}
          >
            {t.settings.account.billingManage}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => go("checkout")}
            className="bg-brand-gradient text-white hover:opacity-90"
          >
            {t.settings.account.billingUpgrade}
          </Button>
        )}
      </div>
    </SettingsSection>
  );
}
