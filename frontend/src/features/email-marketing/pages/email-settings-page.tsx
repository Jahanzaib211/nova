"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { Field, Problems } from "../components/panel-bits";
import { useBridges, useEmMutations, useLists } from "../hooks";

export function EmailSettingsPage() {
  const { t } = useI18n();
  const s = t.features.email.settings;
  const { data: bridges, isLoading, isError } = useBridges();
  const { data: lists } = useLists();
  const m = useEmMutations();
  const [domain, setDomain] = useState("");
  const [password, setPassword] = useState("");
  const [listId, setListId] = useState("");
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);
  return (
    <div className="space-y-8">
      <SettingsSection title={s.title} description={s.description}>
        <Button asChild variant="outline" size="sm">
          <Link href="/workspace/email">{s.openPage}</Link>
        </Button>
      </SettingsSection>
      <SettingsSection
        title={s.bridgesTitle}
        description={s.bridgesDescription}
      >
        {isLoading ? (
          <div
            className="bg-muted/40 h-16 animate-pulse rounded-lg"
            aria-busy="true"
          />
        ) : isError || !bridges ? (
          <p role="alert" className="text-destructive text-sm">
            {s.bridgesUnavailable}
          </p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-3" data-testid="em-bridges">
            {(["mailcow", "twenty", "chatwoot"] as const).map((name) => {
              const b = bridges[name];
              return (
                <li
                  key={name}
                  data-testid={`em-bridge-${name}`}
                  data-configured={b.configured}
                  className="border-panel-border bg-panel space-y-2 rounded-lg border p-3 text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{s.bridge[name]}</span>
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 text-xs",
                        b.configured ? "text-success" : "text-muted-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "size-2 rounded-full",
                          b.configured
                            ? "bg-success"
                            : "bg-muted-foreground/40",
                        )}
                        aria-hidden
                      />
                      {b.configured ? s.configured : s.notConfigured}
                    </span>
                  </div>
                  {b.endpoint && (
                    <p className="text-muted-foreground truncate font-mono text-[11px]">
                      {b.endpoint}
                    </p>
                  )}
                  <Problems items={b.problems} />
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>
      {bridges?.mailcow.configured && (
        <SettingsSection
          title={s.mailcowTitle}
          description={s.mailcowDescription}
        >
          <form
            aria-label={s.ensureSender}
            className="flex flex-col gap-2 sm:flex-row sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              m.mailcowEnsureSender
                .mutateAsync([
                  { domain: domain.trim(), mailbox_password: password },
                ])
                .then((r) =>
                  toast.success(s.senderReady(r.mailbox, r.dkim.configured)),
                )
                .catch(fail);
            }}
          >
            <Field label={s.domain}>
              <Input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="cloud.example.com"
                required
              />
            </Field>
            <Field label={s.mailboxPassword}>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                required
                autoComplete="new-password"
              />
            </Field>
            <Button
              type="submit"
              size="sm"
              disabled={m.mailcowEnsureSender.isPending}
            >
              {s.ensureSender}
            </Button>
          </form>
        </SettingsSection>
      )}
      {bridges?.twenty.configured && (
        <SettingsSection
          title={s.twentyTitle}
          description={s.twentyDescription}
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Field label={s.list}>
              <select
                className="bg-background border-input h-8 rounded-md border px-2 text-xs"
                value={listId}
                onChange={(e) => setListId(e.target.value)}
                aria-label={s.list}
              >
                <option value="">—</option>
                {lists?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              size="sm"
              variant="outline"
              disabled={!listId || m.twentySync.isPending}
              onClick={() =>
                m.twentySync
                  .mutateAsync([listId])
                  .then(() => toast.success(s.jobStarted))
                  .catch(fail)
              }
            >
              {s.twentySync}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!listId || m.twentyImport.isPending}
              onClick={() =>
                m.twentyImport
                  .mutateAsync([listId])
                  .then(() => toast.success(s.jobStarted))
                  .catch(fail)
              }
            >
              {s.twentyImport}
            </Button>
          </div>
        </SettingsSection>
      )}
    </div>
  );
}
