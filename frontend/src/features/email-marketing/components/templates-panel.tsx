"use client";

import { EyeIcon, PlusIcon, SaveIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";

import { type EmTemplate, previewTemplate } from "../api";
import { useEmMutations, useTemplates } from "../hooks";

import { Empty, Field } from "./panel-bits";

const STARTER = `<h1>Hello {{ contact.first_name }}</h1>
<p>Write your message here. Links are tracked automatically.</p>
<p><a href="https://example.com">Read more</a></p>`;

export function TemplatesPanel() {
  const { t } = useI18n();
  const s = t.features.email.templates;
  const { data: templates, isLoading } = useTemplates();
  const m = useEmMutations();
  const [editing, setEditing] = useState<Partial<EmTemplate> | null>(null);
  const [preview, setPreview] = useState<{
    subject: string;
    html: string;
  } | null>(null);
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);

  const save = () => {
    if (!editing) return;
    const body = {
      name: editing.name ?? "",
      subject: editing.subject ?? "",
      html: editing.html ?? "",
      text: editing.text ?? null,
    };
    const op = editing.id
      ? m.updateTemplate.mutateAsync([editing.id, body])
      : m.createTemplate.mutateAsync([body]);
    op.then(() => {
      toast.success(s.saved);
      setEditing(null);
      setPreview(null);
    }).catch(fail);
  };

  return (
    <section className="space-y-4" data-testid="em-templates">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground text-xs">{s.hint}</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            setEditing({ name: "", subject: "", html: STARTER, text: null })
          }
        >
          <PlusIcon className="size-3.5" />
          {s.new}
        </Button>
      </div>
      {editing && (
        <form
          aria-label={s.editor}
          className="border-panel-border grid gap-3 rounded-lg border p-3 lg:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className="space-y-3">
            <Field label={s.name}>
              <Input
                value={editing.name ?? ""}
                onChange={(e) =>
                  setEditing({ ...editing, name: e.target.value })
                }
                required
              />
            </Field>
            <Field label={s.subject}>
              <Input
                value={editing.subject ?? ""}
                onChange={(e) =>
                  setEditing({ ...editing, subject: e.target.value })
                }
                required
              />
            </Field>
            <Field label={s.html}>
              <Textarea
                rows={14}
                className="font-mono text-xs"
                value={editing.html ?? ""}
                onChange={(e) =>
                  setEditing({ ...editing, html: e.target.value })
                }
              />
            </Field>
            <div className="flex gap-2">
              <Button
                type="submit"
                size="sm"
                disabled={
                  m.createTemplate.isPending || m.updateTemplate.isPending
                }
              >
                <SaveIcon className="size-3.5" />
                {s.save}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!editing.id}
                onClick={() =>
                  editing.id &&
                  previewTemplate(editing.id).then(setPreview).catch(fail)
                }
              >
                <EyeIcon className="size-3.5" />
                {s.preview}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setEditing(null)}
              >
                {s.cancel}
              </Button>
            </div>
            {!editing.id && (
              <p className="text-muted-foreground text-[11px]">
                {s.previewAfterSave}
              </p>
            )}
          </div>
          <div className="border-panel-border bg-panel min-h-64 rounded-md border">
            {preview ? (
              <>
                <div className="border-panel-border border-b px-3 py-2 text-xs">
                  {preview.subject}
                </div>
                {/* The rendered HTML is user-authored: sandboxed, no scripts, no navigation. */}
                <iframe
                  title={s.preview}
                  sandbox=""
                  srcDoc={preview.html}
                  className="h-80 w-full bg-white"
                  data-testid="em-template-preview"
                />
              </>
            ) : (
              <div className="text-muted-foreground flex h-full min-h-64 items-center justify-center text-xs">
                {s.noPreview}
              </div>
            )}
          </div>
        </form>
      )}
      {isLoading ? (
        <div
          className="bg-muted/40 h-16 animate-pulse rounded-lg"
          aria-busy="true"
        />
      ) : !templates?.length ? (
        <Empty testId="em-templates-empty">{s.empty}</Empty>
      ) : (
        <ul className="divide-panel-border border-panel-border divide-y rounded-lg border">
          {templates.map((tpl) => (
            <li
              key={tpl.id}
              data-testid="em-template-row"
              className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => {
                  setEditing(tpl);
                  setPreview(null);
                }}
              >
                <div className="truncate font-medium">{tpl.name}</div>
                <div className="text-muted-foreground truncate text-xs">
                  {tpl.subject} · v{tpl.version}
                </div>
              </button>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`${s.delete} ${tpl.name}`}
                onClick={() =>
                  m.deleteTemplate
                    .mutateAsync([tpl.id])
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
