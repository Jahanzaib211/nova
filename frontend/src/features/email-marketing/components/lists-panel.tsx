"use client";

import { Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";

import { useEmMutations, useLists } from "../hooks";

import { Empty } from "./panel-bits";

export function ListsPanel() {
  const { t } = useI18n();
  const s = t.features.email.lists;
  const { data: lists, isLoading } = useLists();
  const m = useEmMutations();
  const [name, setName] = useState("");
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);
  return (
    <section className="space-y-4" data-testid="em-lists">
      <form
        aria-label={s.create}
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return;
          m.createList
            .mutateAsync([{ name: name.trim() }])
            .then(() => {
              toast.success(s.created);
              setName("");
            })
            .catch(fail);
        }}
      >
        <Input
          placeholder={s.namePlaceholder}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="sm:max-w-xs"
        />
        <Button
          type="submit"
          size="sm"
          disabled={!name.trim() || m.createList.isPending}
        >
          {s.create}
        </Button>
      </form>
      {isLoading ? (
        <div
          className="bg-muted/40 h-16 animate-pulse rounded-lg"
          aria-busy="true"
        />
      ) : !lists?.length ? (
        <Empty testId="em-lists-empty">{s.empty}</Empty>
      ) : (
        <ul className="divide-panel-border border-panel-border divide-y rounded-lg border">
          {lists.map((l) => (
            <li
              key={l.id}
              data-testid="em-list-row"
              className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <div className="truncate font-medium">{l.name}</div>
                <div className="text-muted-foreground text-xs tabular-nums">
                  {s.members(l.member_count)}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`${s.delete} ${l.name}`}
                onClick={() =>
                  m.deleteList
                    .mutateAsync([l.id])
                    .then(() => toast.success(s.deleted))
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
