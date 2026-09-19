"use client";

import { Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";

import { useEmMutations, useSuppressions } from "../hooks";

import { Empty } from "./panel-bits";

export function SuppressionsPanel() {
  const { t } = useI18n();
  const s = t.features.email.suppressions;
  const { data, isLoading } = useSuppressions();
  const m = useEmMutations();
  const [email, setEmail] = useState("");
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);
  return (
    <section className="space-y-4" data-testid="em-suppressions">
      <p className="text-muted-foreground text-xs">{s.hint}</p>
      <form
        aria-label={s.add}
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          m.addSuppression
            .mutateAsync([{ email: email.trim(), reason: "manual" }])
            .then(() => {
              toast.success(s.added);
              setEmail("");
            })
            .catch(fail);
        }}
      >
        <Input
          type="email"
          placeholder={s.emailPlaceholder}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="sm:max-w-xs"
        />
        <Button
          type="submit"
          size="sm"
          disabled={!email.includes("@") || m.addSuppression.isPending}
        >
          {s.add}
        </Button>
      </form>
      {isLoading ? (
        <div
          className="bg-muted/40 h-16 animate-pulse rounded-lg"
          aria-busy="true"
        />
      ) : !data?.length ? (
        <Empty testId="em-suppressions-empty">{s.empty}</Empty>
      ) : (
        <ul className="divide-panel-border border-panel-border divide-y rounded-lg border">
          {data.map((row) => (
            <li
              key={row.email}
              data-testid="em-suppression-row"
              className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-xs">{row.email}</div>
                <div className="text-muted-foreground text-xs">
                  {t.features.email.suppressionReason[row.reason]}
                  {row.detail ? ` · ${row.detail}` : ""}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`${s.remove} ${row.email}`}
                onClick={() =>
                  m.removeSuppression
                    .mutateAsync([row.email])
                    .then(() => toast.success(s.removed))
                    .catch(fail)
                }
              >
                <Trash2Icon className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
