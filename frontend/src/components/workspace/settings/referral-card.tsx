"use client";

import { CheckIcon, CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

interface Referral {
  code: string;
  referral_count: number;
  bonus_daily_tokens: number;
}

export function ReferralCard() {
  const { t } = useI18n();
  const [referral, setReferral] = useState<Referral | null>(null);
  const [link, setLink] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/v1/referral", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: Referral) => {
        setReferral(data);
        setLink(`${window.location.origin}/signup?ref=${data.code}`);
      })
      .catch(() => {
        // Non-critical; hide the card on failure.
      });
    return () => controller.abort();
  }, []);

  if (!referral) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore.
    }
  };

  return (
    <SettingsSection
      title={t.settings.account.referralTitle}
      description={t.settings.account.referralDescription}
    >
      <div className="max-w-md space-y-3">
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">
            {t.settings.account.referralYourLink}
          </span>
          <div className="flex gap-2">
            <Input readOnly value={link} className="font-mono text-xs" />
            <Button
              variant="outline"
              size="sm"
              onClick={handleCopy}
              className="shrink-0 gap-1.5"
            >
              {copied ? (
                <CheckIcon className="size-3.5" />
              ) : (
                <CopyIcon className="size-3.5" />
              )}
              {copied
                ? t.settings.account.referralCopied
                : t.settings.account.referralCopy}
            </Button>
          </div>
        </div>

        <div className="text-muted-foreground flex gap-4 text-sm">
          <span>
            <span className="text-foreground font-semibold tabular-nums">
              {referral.referral_count}
            </span>{" "}
            {t.settings.account.referralCount}
          </span>
          {referral.bonus_daily_tokens > 0 && (
            <span>
              <span className="text-foreground font-semibold tabular-nums">
                +{referral.bonus_daily_tokens.toLocaleString()}
              </span>{" "}
              {t.settings.account.referralBonusActive}
            </span>
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
