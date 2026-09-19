"use client";

import { Trash2Icon, UploadIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import { JobProgress } from "@/features/jobs/components/job-progress";
import { useJobEvents, useJobs } from "@/features/jobs/hooks";

import { guessImportMapping } from "../api";
import { useContacts, useEmMutations, useLists } from "../hooks";

import { Empty, Field } from "./panel-bits";

const FIELDS = ["email", "first_name", "last_name"] as const;

export function ContactsPanel() {
  const { t } = useI18n();
  const s = t.features.email.contacts;
  const [q, setQ] = useState("");
  const { data, isLoading } = useContacts(q);
  const { data: lists } = useLists();
  const m = useEmMutations();
  const [email, setEmail] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [csv, setCsv] = useState("");
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [listId, setListId] = useState<string>("");
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: jobs } = useJobs(null);
  const job = jobs?.find((j) => j.id === jobId) ?? null;
  const { events } = useJobEvents(job, Boolean(job));
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);

  const analyse = async (text: string) => {
    setCsv(text);
    if (!text.trim()) return;
    try {
      const g = await guessImportMapping(text);
      setHeaders(g.headers);
      setMapping(g.mapping);
    } catch (e) {
      fail(e);
    }
  };

  return (
    <section className="space-y-4" data-testid="em-contacts">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          placeholder={s.search}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="sm:max-w-xs"
          aria-label={s.search}
        />
        <form
          aria-label={s.add}
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            m.createContact
              .mutateAsync([{ email: email.trim() }])
              .then(() => {
                toast.success(s.added);
                setEmail("");
              })
              .catch(fail);
          }}
        >
          <Input
            placeholder={s.emailPlaceholder}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="sm:w-56"
          />
          <Button
            type="submit"
            size="sm"
            disabled={!email.includes("@") || m.createContact.isPending}
          >
            {s.add}
          </Button>
        </form>
        <Button
          variant="outline"
          size="sm"
          className="sm:ml-auto"
          onClick={() => setShowImport((v) => !v)}
        >
          <UploadIcon className="size-3.5" />
          {s.importCsv}
        </Button>
      </div>

      {showImport && (
        <div
          className="border-panel-border space-y-3 rounded-lg border p-3"
          data-testid="em-import"
        >
          <Textarea
            rows={5}
            placeholder={s.csvPlaceholder}
            value={csv}
            onChange={(e) => void analyse(e.target.value)}
            className="font-mono text-xs"
            aria-label={s.csvPlaceholder}
          />
          {headers.length > 0 && (
            <div className="grid gap-2 sm:grid-cols-4">
              {FIELDS.map((f) => (
                <Field key={f} label={s.fields[f]}>
                  <select
                    className="bg-background border-input h-8 rounded-md border px-2 text-xs"
                    value={mapping[f] ?? ""}
                    onChange={(e) =>
                      setMapping({ ...mapping, [f]: e.target.value })
                    }
                    aria-label={s.fields[f]}
                  >
                    <option value="">—</option>
                    {headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
              <Field label={s.intoList}>
                <select
                  className="bg-background border-input h-8 rounded-md border px-2 text-xs"
                  value={listId}
                  onChange={(e) => setListId(e.target.value)}
                  aria-label={s.intoList}
                >
                  <option value="">—</option>
                  {lists?.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          )}
          <div className="flex items-center gap-3">
            <Button
              size="sm"
              disabled={!mapping.email || m.importContacts.isPending}
              onClick={() =>
                m.importContacts
                  .mutateAsync([
                    {
                      csv_text: csv,
                      mapping: Object.fromEntries(
                        Object.entries(mapping).filter(([, v]) => v),
                      ),
                      list_id: listId || null,
                    },
                  ])
                  .then((r) => {
                    setJobId(r.job_id);
                    toast.success(
                      s.importStarted(
                        r.preview.valid,
                        r.preview.invalid + r.preview.duplicates,
                      ),
                    );
                  })
                  .catch(fail)
              }
            >
              {s.startImport}
            </Button>
            {job && (
              <div className="min-w-0 flex-1">
                <JobProgress
                  pct={job.progress_pct}
                  status={job.status}
                  message={job.progress_message}
                />
                {events.length > 0 &&
                  typeof events[events.length - 1]?.payload.message ===
                    "string" && (
                    <div className="text-muted-foreground truncate text-[11px]">
                      {String(events[events.length - 1]?.payload.message)}
                    </div>
                  )}
              </div>
            )}
          </div>
        </div>
      )}

      {isLoading ? (
        <div
          className="bg-muted/40 h-24 animate-pulse rounded-lg"
          aria-busy="true"
        />
      ) : !data?.contacts.length ? (
        <Empty testId="em-contacts-empty">{s.empty}</Empty>
      ) : (
        <div className="border-panel-border overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="text-muted-foreground text-left text-xs">
              <tr>
                <th className="px-3 py-2 font-medium">{s.columns.email}</th>
                <th className="px-3 py-2 font-medium">{s.columns.name}</th>
                <th className="px-3 py-2 font-medium">{s.columns.status}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-panel-border divide-y">
              {data.contacts.map((c) => (
                <tr
                  key={c.id}
                  data-testid="em-contact-row"
                  data-status={c.status}
                >
                  <td className="px-3 py-2 font-mono text-xs">{c.email}</td>
                  <td className="px-3 py-2">
                    {[c.first_name, c.last_name].filter(Boolean).join(" ") ||
                      "—"}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {t.features.email.contactStatus[c.status]}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`${s.delete} ${c.email}`}
                      onClick={() =>
                        m.deleteContact
                          .mutateAsync([c.id])
                          .then(() => toast.success(s.deleted))
                          .catch(fail)
                      }
                    >
                      <Trash2Icon className="size-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-muted-foreground px-3 py-2 text-xs tabular-nums">
            {s.total(data.total)}
          </div>
        </div>
      )}
    </section>
  );
}
