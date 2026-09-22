"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

interface ByokStatus {
  enabled: boolean;
  has_key: boolean;
  provider: string | null;
}

export function ByokSettings() {
  const { t } = useI18n();
  const [status, setStatus] = useState<ByokStatus | null>(null);
  const [provider, setProvider] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = async (signal?: AbortSignal) => {
    const res = await fetch("/api/v1/byok", { signal });
    if (res.ok) setStatus((await res.json()) as ByokStatus);
  };

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch(() => undefined);
    return () => controller.abort();
  }, []);

  // Feature is off on this instance → render nothing.
  if (!status?.enabled) return null;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const res = await fetch("/api/v1/byok", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
        body: JSON.stringify({ provider, api_key: apiKey }),
      });
      if (res.ok) {
        setApiKey("");
        setMessage(t.settings.account.byokSaved);
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    setMessage("");
    try {
      await fetch("/api/v1/byok", {
        method: "DELETE",
        headers: { ...getCsrfHeaders() },
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection
      title={t.settings.account.byokTitle}
      description={t.settings.account.byokDescription}
    >
      {status.has_key && (
        <p className="text-success dark:text-success mb-3 text-sm">
          {t.settings.account.byokActive}
          {status.provider ? ` (${status.provider})` : ""}
        </p>
      )}
      <form onSubmit={handleSave} className="max-w-sm space-y-3">
        <Input
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          placeholder={t.settings.account.byokProvider}
        />
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={t.settings.account.byokApiKey}
          required
          minLength={8}
        />
        {message && <p className="text-success text-sm">{message}</p>}
        <div className="flex gap-2">
          <Button type="submit" variant="outline" size="sm" disabled={busy}>
            {t.settings.account.byokSave}
          </Button>
          {status.has_key && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={handleRemove}
            >
              {t.settings.account.byokRemove}
            </Button>
          )}
        </div>
      </form>
    </SettingsSection>
  );
}
