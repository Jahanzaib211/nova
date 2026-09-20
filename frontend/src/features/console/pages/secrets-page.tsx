"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability, useCapabilityMutation } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { OpTable, StatusDot, tsToLocal } from "../components/op-table";

/** Secrets: presence-only inventory; values are written, never read back. */
export function SecretsPage() {
  const { t } = useI18n();
  const s = t.features.console.secrets;
  const list = useCapability("secrets.list", {});
  const set = useCapabilityMutation("secrets.set", ["secrets.list"]);
  const unset = useCapabilityMutation("secrets.unset", ["secrets.list"]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");

  return (
    <SettingsSection title={s.title} description={s.description}>
      <form
        className="mb-4 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim() || !value) return;
          set.mutate(
            { name: name.trim(), value },
            { onSuccess: () => setValue("") },
          );
        }}
      >
        <label className="flex min-w-40 flex-col gap-1 text-xs">
          {s.name}
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="mailcow_api_key"
          />
        </label>
        <label className="flex min-w-64 flex-1 flex-col gap-1 text-xs">
          {s.value}
          <Input
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <Button
          type="submit"
          size="sm"
          disabled={set.isPending || !name.trim() || !value}
        >
          {s.write}
        </Button>
      </form>
      {set.isError ? (
        <p role="alert" className="text-destructive mb-3 text-sm">
          {set.error.message}
        </p>
      ) : null}
      {list.isLoading ? (
        <p className="text-muted-foreground text-sm">{t.common.loading}</p>
      ) : list.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {s.adminOnly}
        </p>
      ) : (
        <OpTable
          empty={s.empty}
          keyOf={(r) => `${String(r.source)}:${String(r.name)}`}
          rows={list.data?.items ?? []}
          columns={[
            {
              key: "present",
              label: "",
              render: (r) => (
                <StatusDot
                  tone={r.present ? (r.secure ? "ok" : "warn") : "off"}
                />
              ),
            },
            {
              key: "name",
              label: s.name,
              render: (r) => (
                <code className="font-mono text-xs">{String(r.name)}</code>
              ),
            },
            { key: "source", label: s.source },
            { key: "mode", label: s.mode },
            {
              key: "modified_at",
              label: s.modified,
              render: (r) => tsToLocal(r.modified_at),
            },
            {
              key: "actions",
              label: "",
              render: (r) =>
                r.source === "file" ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => unset.mutate({ name: String(r.name) })}
                    disabled={unset.isPending}
                  >
                    {s.remove}
                  </Button>
                ) : null,
            },
          ]}
        />
      )}
      <p className="text-muted-foreground mt-3 text-xs">{s.envHint}</p>
    </SettingsSection>
  );
}
