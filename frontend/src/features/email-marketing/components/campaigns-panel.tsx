"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { EmCampaign } from "../api";
import {
  useCampaignEvents,
  useCampaignStats,
  useCampaigns,
  useEmMutations,
  useLists,
  usePreflight,
  useTemplates,
} from "../hooks";
import {
  CAMPAIGN_ACTIONS,
  TONE_CLASS,
  campaignTone,
  progressPct,
  rate,
} from "../status";

import { Empty, Field, Pill, Problems } from "./panel-bits";

export function CampaignsPanel() {
  const { t } = useI18n();
  const s = t.features.email.campaigns;
  const { data: campaigns, isLoading } = useCampaigns();
  const { data: lists } = useLists();
  const { data: templates } = useTemplates();
  const m = useEmMutations();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    name: "",
    list_id: "",
    template_id: "",
    from_email: "",
    from_name: "",
  });
  const [openId, setOpenId] = useState<string | null>(null);
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);
  const open = campaigns?.find((c) => c.id === openId) ?? null;

  return (
    <section className="space-y-4" data-testid="em-campaigns">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground text-xs">{s.hint}</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCreating((v) => !v)}
        >
          <PlusIcon className="size-3.5" />
          {s.new}
        </Button>
      </div>
      {creating && (
        <form
          aria-label={s.new}
          className="border-panel-border grid gap-3 rounded-lg border p-3 sm:grid-cols-2 lg:grid-cols-3"
          onSubmit={(e) => {
            e.preventDefault();
            m.createCampaign
              .mutateAsync([form])
              .then((c) => {
                toast.success(s.created);
                setCreating(false);
                setOpenId(c.id);
              })
              .catch(fail);
          }}
        >
          <Field label={s.name}>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </Field>
          <Field label={s.list}>
            <select
              className="bg-background border-input h-8 rounded-md border px-2 text-xs"
              value={form.list_id}
              onChange={(e) => setForm({ ...form, list_id: e.target.value })}
              required
              aria-label={s.list}
            >
              <option value="">—</option>
              {lists?.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.member_count})
                </option>
              ))}
            </select>
          </Field>
          <Field label={s.template}>
            <select
              className="bg-background border-input h-8 rounded-md border px-2 text-xs"
              value={form.template_id}
              onChange={(e) =>
                setForm({ ...form, template_id: e.target.value })
              }
              required
              aria-label={s.template}
            >
              <option value="">—</option>
              {templates?.map((tpl) => (
                <option key={tpl.id} value={tpl.id}>
                  {tpl.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={s.fromEmail}>
            <Input
              type="email"
              value={form.from_email}
              onChange={(e) => setForm({ ...form, from_email: e.target.value })}
              required
            />
          </Field>
          <Field label={s.fromName}>
            <Input
              value={form.from_name}
              onChange={(e) => setForm({ ...form, from_name: e.target.value })}
            />
          </Field>
          <div className="flex items-end gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={m.createCampaign.isPending}
            >
              {s.create}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setCreating(false)}
            >
              {s.cancel}
            </Button>
          </div>
        </form>
      )}
      {isLoading ? (
        <div
          className="bg-muted/40 h-16 animate-pulse rounded-lg"
          aria-busy="true"
        />
      ) : !campaigns?.length ? (
        <Empty testId="em-campaigns-empty">{s.empty}</Empty>
      ) : (
        <ul className="divide-panel-border border-panel-border divide-y rounded-lg border">
          {campaigns.map((c) => (
            <li key={c.id} data-testid="em-campaign-row" data-status={c.status}>
              <div className="flex items-center gap-3 px-3 py-2 text-sm">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setOpenId(openId === c.id ? null : c.id)}
                  aria-expanded={openId === c.id}
                >
                  <div className="truncate font-medium">{c.name}</div>
                  <div className="text-muted-foreground truncate text-xs">
                    {c.from_name
                      ? `${c.from_name} <${c.from_email}>`
                      : c.from_email}
                    {c.stats.recipients
                      ? ` · ${s.recipients(c.stats.recipients)}`
                      : ""}
                  </div>
                </button>
                <Pill
                  tone={TONE_CLASS[campaignTone(c.status)]}
                  testId="em-campaign-status"
                >
                  {t.features.email.campaignStatus[c.status]}
                </Pill>
                {(c.status === "draft" ||
                  c.status === "completed" ||
                  c.status === "cancelled" ||
                  c.status === "failed") && (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`${s.delete} ${c.name}`}
                    onClick={() =>
                      m.deleteCampaign
                        .mutateAsync([c.id])
                        .then(() => toast.success(s.deleted))
                        .catch(fail)
                    }
                  >
                    <Trash2Icon className="size-3.5" />
                  </Button>
                )}
              </div>
              {open?.id === c.id && <CampaignDetail campaign={c} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CampaignDetail({ campaign }: { campaign: EmCampaign }) {
  const { t } = useI18n();
  const s = t.features.email.campaigns;
  const m = useEmMutations();
  const live = campaign.status === "sending";
  const { data: stats } = useCampaignStats(campaign.id, live);
  const { data: preflight } = usePreflight(
    campaign.status === "draft" || campaign.status === "scheduled"
      ? campaign.id
      : null,
  );
  const { data: events } = useCampaignEvents(campaign.id);
  const [testTo, setTestTo] = useState("");
  const fail = (e: unknown) =>
    toast.error(e instanceof Error ? e.message : t.features.email.failed);
  const st = stats ?? campaign.stats;
  const pct = progressPct(st);
  const tiles: Array<[string, number | null | undefined, string | null]> = [
    [s.stats.sent, st.sent, null],
    [
      s.stats.opened,
      st.opened,
      rate(st.opened, st.sent) === null ? null : `${rate(st.opened, st.sent)}%`,
    ],
    [
      s.stats.clicked,
      st.clicked,
      rate(st.clicked, st.sent) === null
        ? null
        : `${rate(st.clicked, st.sent)}%`,
    ],
    [s.stats.bounced, (st.bounced_hard ?? 0) + (st.bounced_soft ?? 0), null],
    [s.stats.unsubscribed, st.unsubscribed, null],
    [s.stats.failed, st.failed, null],
  ];
  return (
    <div
      className="border-panel-border bg-panel/50 space-y-3 border-t px-3 py-3"
      data-testid="em-campaign-detail"
    >
      {campaign.error && (
        <p role="alert" className="text-destructive text-xs">
          {campaign.error}
        </p>
      )}
      {preflight && (
        <div className="space-y-1 text-xs" data-testid="em-preflight">
          <div
            className={cn(
              "font-medium",
              preflight.ok
                ? "text-success"
                : "text-warning-foreground dark:text-warning",
            )}
          >
            {preflight.ok
              ? s.preflightOk(preflight.recipients, preflight.suppressed)
              : s.preflightBlocked}
          </div>
          <Problems items={preflight.problems} />
        </div>
      )}
      {pct !== null && (
        <div className="space-y-1" data-testid="em-campaign-progress">
          <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
            <div
              className={cn(
                "h-full rounded-full",
                live ? "bg-info" : "bg-success",
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-muted-foreground text-[11px] tabular-nums">
            {s.progress(pct, st.queued ?? 0)}
          </div>
        </div>
      )}
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {tiles.map(([label, value, sub]) => (
          <div
            key={label}
            className="border-panel-border rounded-md border px-2 py-1.5"
          >
            <div className="text-muted-foreground text-[10px] tracking-wide uppercase">
              {label}
            </div>
            <div className="font-mono text-sm tabular-nums">{value ?? 0}</div>
            {sub && (
              <div className="text-muted-foreground text-[10px] tabular-nums">
                {sub}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {CAMPAIGN_ACTIONS[campaign.status].map((action) => (
          <Button
            key={action}
            size="sm"
            variant={action === "send-now" ? "default" : "outline"}
            disabled={
              m.campaignAction.isPending ||
              (action === "send-now" &&
                preflight !== undefined &&
                !preflight.ok)
            }
            onClick={() =>
              m.campaignAction
                .mutateAsync([campaign.id, action])
                .then(() => toast.success(s.actionDone[action]))
                .catch(fail)
            }
          >
            {s.actions[action]}
          </Button>
        ))}
        <form
          className="ml-auto flex items-center gap-2"
          aria-label={s.testSend}
          onSubmit={(e) => {
            e.preventDefault();
            m.campaignTestSend
              .mutateAsync([campaign.id, testTo.trim()])
              .then((r) =>
                r.status === "sent"
                  ? toast.success(s.testSent(testTo))
                  : toast.error(r.error ?? r.status),
              )
              .catch(fail);
          }}
        >
          <Input
            type="email"
            placeholder={s.testTo}
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            className="h-8 w-48 text-xs"
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={!testTo.includes("@") || m.campaignTestSend.isPending}
          >
            {s.testSend}
          </Button>
        </form>
      </div>
      {events && events.length > 0 && (
        <ul
          className="text-muted-foreground max-h-40 space-y-0.5 overflow-y-auto font-mono text-[11px]"
          data-testid="em-campaign-events"
        >
          {events.map((e) => (
            <li key={e.id}>
              <time dateTime={e.created_at}>
                {e.created_at.replace("T", " ").slice(0, 19)}
              </time>{" "}
              · {e.type}
              {typeof e.payload.error === "string"
                ? ` · ${e.payload.error}`
                : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
