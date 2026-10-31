"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability, useCapabilityMutation } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { OpTable, tsToLocal } from "../components/op-table";

/** Devices & sessions: browser sessions and harness tokens for external harnesses. */
export function DevicesPage() {
  const { t } = useI18n();
  const s = t.features.console.devices;
  const session = useCapability("sessions.get", {});
  const tokens = useCapability("sessions.tokens", {});
  const revokeAll = useCapabilityMutation("sessions.revoke_all", [
    "sessions.get",
  ]);
  const create = useCapabilityMutation("sessions.token_create", [
    "sessions.tokens",
  ]);
  const revoke = useCapabilityMutation("sessions.token_revoke", [
    "sessions.tokens",
  ]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("*");
  const minted = create.data;

  return (
    <div className="space-y-8">
      <SettingsSection
        title={s.sessionsTitle}
        description={s.sessionsDescription}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => revokeAll.mutate({})}
            disabled={revokeAll.isPending}
          >
            {s.signOutEverywhere}
          </Button>
        }
      >
        {session.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : session.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {s.loadFailed}
          </p>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            <dt className="text-muted-foreground">{s.lastSignIn}</dt>
            <dd>{tsToLocal(session.data?.last_sign_in_at)}</dd>
            <dt className="text-muted-foreground">{s.tokenVersion}</dt>
            <dd className="tabular-nums">{session.data?.token_version}</dd>
          </dl>
        )}
        {revokeAll.isSuccess ? (
          <p className="text-success mt-2 text-sm">{s.signedOut}</p>
        ) : null}
      </SettingsSection>

      <SettingsSection title={s.tokensTitle} description={s.tokensDescription}>
        <form
          className="mb-4 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            create.mutate({
              name: name.trim(),
              scopes: scopes
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean),
            });
          }}
        >
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs">
            {s.tokenName}
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={s.tokenNamePlaceholder}
            />
          </label>
          <label className="flex min-w-40 flex-col gap-1 text-xs">
            {s.tokenScopes}
            <Input
              value={scopes}
              onChange={(e) => setScopes(e.target.value)}
              placeholder="*"
            />
          </label>
          <Button
            type="submit"
            size="sm"
            disabled={create.isPending || !name.trim()}
          >
            {s.mint}
          </Button>
        </form>
        {create.isError ? (
          <p role="alert" className="text-destructive mb-3 text-sm">
            {String(create.error.message)}
          </p>
        ) : null}
        {minted ? (
          <div
            className="bg-muted/40 mb-4 rounded-md border p-3 text-sm"
            data-testid="minted-token"
          >
            <p className="mb-1 font-medium">{s.mintedOnce}</p>
            <code className="block font-mono text-xs break-all select-all">
              {minted.token}
            </code>
            <p className="text-muted-foreground mt-2 text-xs">
              {s.mintedHint} <code className="font-mono">{minted.mcp_url}</code>
            </p>
          </div>
        ) : null}
        {tokens.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : (
          <OpTable
            empty={s.tokensEmpty}
            keyOf={(r) => String(r.id)}
            rows={(tokens.data?.items ?? []).filter(
              // Per-turn runtime tokens are minted and revoked by the
              // gateway itself; once revoked they are noise here.
              (r) => !(r.revoked_at && String(r.name).startsWith("runtime:")),
            )}
            columns={[
              { key: "name", label: s.colName },
              {
                key: "prefix",
                label: s.colPrefix,
                render: (r) => (
                  <code className="font-mono text-xs">{String(r.prefix)}…</code>
                ),
              },
              {
                key: "scopes",
                label: s.colScopes,
                render: (r) => (r.scopes as string[]).join(", "),
              },
              {
                key: "last_used_at",
                label: s.colLastUsed,
                render: (r) => tsToLocal(r.last_used_at),
              },
              {
                key: "revoked_at",
                label: s.colStatus,
                render: (r) => (r.revoked_at ? s.revoked : s.active),
              },
              {
                key: "actions",
                label: "",
                render: (r) =>
                  r.revoked_at ? null : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => revoke.mutate({ token_id: String(r.id) })}
                      disabled={revoke.isPending}
                    >
                      {s.revoke}
                    </Button>
                  ),
              },
            ]}
          />
        )}
      </SettingsSection>
    </div>
  );
}
