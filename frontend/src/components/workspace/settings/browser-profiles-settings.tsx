"use client";

import { Button } from "@/components/ui/button";
import { useBrowserProfiles } from "@/core/browser/api";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

// Saved browser logins. Metadata only: the API never returns cookie values.
export function BrowserProfilesSettings() {
  const { t } = useI18n();
  const tb = t.features.browser;
  const { data, remove, makeDefault, clearDefault } = useBrowserProfiles();

  if (!data) return null;

  return (
    <SettingsSection
      title={tb.profilesTitle}
      description={tb.profilesDescription}
    >
      {!data.enabled ? (
        <p className="text-muted-foreground text-sm">{tb.profilesDisabled}</p>
      ) : data.profiles.length === 0 ? (
        <p className="text-muted-foreground text-sm">{tb.profilesEmpty}</p>
      ) : (
        <ul
          className="divide-border divide-y rounded-md border"
          data-testid="browser-profiles"
        >
          {data.profiles.map((p) => (
            <li
              key={p.name}
              className="flex flex-wrap items-center gap-2 px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm font-medium">
                  {p.name}
                  {p.is_default && (
                    <span className="bg-muted rounded px-1.5 py-0.5 text-[10px] uppercase">
                      {tb.isDefault}
                    </span>
                  )}
                </div>
                <div className="text-muted-foreground truncate text-xs">
                  {p.domains.join(", ")} · {p.cookie_count} {tb.cookies}
                  {p.corrupt ? ` · ${tb.corrupt}` : ""}
                </div>
              </div>
              {p.is_default ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void clearDefault()}
                >
                  {tb.clearDefault}
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={p.corrupt}
                  onClick={() => void makeDefault(p.name)}
                >
                  {tb.makeDefault}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => void remove(p.name)}
              >
                {tb.remove}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  );
}
