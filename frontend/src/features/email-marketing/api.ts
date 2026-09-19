/** REST client for /api/em (owner-scoped). Shapes mirror routers/email_marketing.py. */
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CampaignStatus,
  ContactStatus,
  EmailEventType,
  SuppressionReason,
} from "./types";

export interface EmList {
  id: string;
  name: string;
  description: string | null;
  member_count: number;
  created_at: string;
  updated_at: string;
}
export interface EmContact {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  status: ContactStatus;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface EmTemplate {
  id: string;
  name: string;
  subject: string;
  html: string;
  text: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}
export interface EmCampaign {
  id: string;
  name: string;
  list_id: string;
  template_id: string;
  from_email: string;
  from_name: string;
  reply_to: string | null;
  status: CampaignStatus;
  scheduled_at: string | null;
  throttle_per_minute: number | null;
  stats: Partial<CampaignStats>;
  job_id: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface CampaignStats {
  recipients: number;
  queued: number;
  sending: number;
  sent: number;
  deferred: number;
  failed: number;
  suppressed: number;
  opened: number;
  clicked: number;
  bounced_hard: number;
  bounced_soft: number;
  complained: number;
  unsubscribed: number;
}
export interface Preflight {
  ok: boolean;
  problems: string[];
  recipients: number;
  suppressed: number;
  list: string | null;
  template: string | null;
}
export interface EmEvent {
  id: number;
  type: EmailEventType;
  campaign_id: string | null;
  send_id: string | null;
  contact_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface Suppression {
  email: string;
  reason: SuppressionReason;
  detail: string | null;
  created_at: string;
}
export interface BridgeStatus {
  enabled: boolean;
  configured: boolean;
  problems: string[];
  endpoint: string | null;
}
export type BridgesStatus = Record<
  "mailcow" | "twenty" | "chatwoot",
  BridgeStatus
>;

function base(): string {
  return `${getBackendBaseURL()}/api/em`;
}

async function readJson<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = `${what}: HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

function send(method: string, path: string, body?: unknown): Promise<Response> {
  return fetch(`${base()}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// lists
export const listLists = async () =>
  (await readJson<{ lists: EmList[] }>(await fetch(`${base()}/lists`), "lists"))
    .lists;
export const createList = (body: { name: string; description?: string }) =>
  send("POST", "/lists", body).then((r) => readJson<EmList>(r, "create list"));
export const deleteList = (id: string) =>
  send("DELETE", `/lists/${encodeURIComponent(id)}`).then((r) =>
    readJson<{ ok: boolean }>(r, "delete list"),
  );
export const listMembers = async (id: string) =>
  (
    await readJson<{ contacts: EmContact[] }>(
      await fetch(`${base()}/lists/${encodeURIComponent(id)}/members`),
      "members",
    )
  ).contacts;
export const addMembers = (id: string, contact_ids: string[]) =>
  send("POST", `/lists/${encodeURIComponent(id)}/members`, {
    contact_ids,
  }).then((r) => readJson<{ added: number }>(r, "add members"));

// contacts
export const listContacts = async (q?: string) => {
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  qs.set("limit", "200");
  return readJson<{ contacts: EmContact[]; total: number }>(
    await fetch(`${base()}/contacts?${qs}`),
    "contacts",
  );
};
export const createContact = (body: {
  email: string;
  first_name?: string;
  last_name?: string;
}) =>
  send("POST", "/contacts", body).then((r) =>
    readJson<EmContact>(r, "create contact"),
  );
export const deleteContact = (id: string) =>
  send("DELETE", `/contacts/${encodeURIComponent(id)}`).then((r) =>
    readJson<{ ok: boolean }>(r, "delete contact"),
  );
export const guessImportMapping = (csv_text: string) =>
  send("POST", "/contacts/import/mapping", { csv_text }).then((r) =>
    readJson<{ headers: string[]; mapping: Record<string, string> }>(
      r,
      "mapping",
    ),
  );
export const importContacts = (body: {
  csv_text: string;
  mapping: Record<string, string>;
  list_id?: string | null;
}) =>
  send("POST", "/contacts/import", body).then((r) =>
    readJson<{
      job_id: string;
      preview: {
        total: number;
        valid: number;
        duplicates: number;
        invalid: number;
      };
    }>(r, "import"),
  );

// templates
export const listTemplates = async () =>
  (
    await readJson<{ templates: EmTemplate[] }>(
      await fetch(`${base()}/templates`),
      "templates",
    )
  ).templates;
export const createTemplate = (body: {
  name: string;
  subject: string;
  html: string;
  text?: string | null;
}) =>
  send("POST", "/templates", body).then((r) =>
    readJson<EmTemplate>(r, "create template"),
  );
export const updateTemplate = (
  id: string,
  body: Partial<{
    name: string;
    subject: string;
    html: string;
    text: string | null;
  }>,
) =>
  send("PATCH", `/templates/${encodeURIComponent(id)}`, body).then((r) =>
    readJson<EmTemplate>(r, "update template"),
  );
export const deleteTemplate = (id: string) =>
  send("DELETE", `/templates/${encodeURIComponent(id)}`).then((r) =>
    readJson<{ ok: boolean }>(r, "delete template"),
  );
export const previewTemplate = (id: string, contact_id?: string) =>
  send("POST", `/templates/${encodeURIComponent(id)}/preview`, {
    contact_id: contact_id ?? null,
  }).then((r) =>
    readJson<{ subject: string; html: string; text: string }>(r, "preview"),
  );

// campaigns
export const listCampaigns = async () =>
  (
    await readJson<{ campaigns: EmCampaign[] }>(
      await fetch(`${base()}/campaigns`),
      "campaigns",
    )
  ).campaigns;
export const createCampaign = (body: {
  name: string;
  list_id: string;
  template_id: string;
  from_email: string;
  from_name: string;
  reply_to?: string | null;
}) =>
  send("POST", "/campaigns", body).then((r) =>
    readJson<EmCampaign>(r, "create campaign"),
  );
export const deleteCampaign = (id: string) =>
  send("DELETE", `/campaigns/${encodeURIComponent(id)}`).then((r) =>
    readJson<{ ok: boolean }>(r, "delete campaign"),
  );
export const campaignPreflight = async (id: string) =>
  readJson<Preflight>(
    await fetch(`${base()}/campaigns/${encodeURIComponent(id)}/preflight`),
    "preflight",
  );
export const campaignAction = (
  id: string,
  action: "send-now" | "pause" | "resume" | "cancel",
) =>
  send("POST", `/campaigns/${encodeURIComponent(id)}/${action}`).then((r) =>
    readJson<EmCampaign>(r, action),
  );
export const campaignTestSend = (id: string, to: string) =>
  send("POST", `/campaigns/${encodeURIComponent(id)}/test-send`, { to }).then(
    (r) =>
      readJson<{
        status: string;
        message_id: string | null;
        error: string | null;
      }>(r, "test send"),
  );
export const campaignStats = async (id: string) =>
  readJson<CampaignStats>(
    await fetch(`${base()}/campaigns/${encodeURIComponent(id)}/stats`),
    "stats",
  );
export const campaignEvents = async (id: string) =>
  (
    await readJson<{ events: EmEvent[] }>(
      await fetch(
        `${base()}/campaigns/${encodeURIComponent(id)}/events?limit=100`,
      ),
      "events",
    )
  ).events;

// suppressions
export const listSuppressions = async () =>
  (
    await readJson<{ suppressions: Suppression[] }>(
      await fetch(`${base()}/suppressions`),
      "suppressions",
    )
  ).suppressions;
export const addSuppression = (body: {
  email: string;
  reason: SuppressionReason;
  detail?: string;
}) =>
  send("POST", "/suppressions", body).then((r) =>
    readJson<Suppression>(r, "suppress"),
  );
export const removeSuppression = (email: string) =>
  send("DELETE", `/suppressions/${encodeURIComponent(email)}`).then((r) =>
    readJson<{ ok: boolean }>(r, "unsuppress"),
  );

// bridges
export const bridgesStatus = async () =>
  (
    await readJson<{ bridges: BridgesStatus }>(
      await fetch(`${base()}/bridges/status`),
      "bridges",
    )
  ).bridges;
export const mailcowEnsureSender = (body: {
  domain: string;
  mailbox_password: string;
}) =>
  send("POST", "/bridges/mailcow/ensure-sender", body).then((r) =>
    readJson<{
      mailbox: string;
      mailbox_created: boolean;
      alias: string;
      alias_created: boolean;
      dkim: { configured: boolean; selector: string | null };
    }>(r, "ensure sender"),
  );
export const twentySync = (list_id: string) =>
  send("POST", "/bridges/twenty/sync-contacts", { list_id }).then((r) =>
    readJson<{ job_id: string }>(r, "twenty sync"),
  );
export const twentyImport = (list_id: string) =>
  send("POST", "/bridges/twenty/import", { list_id }).then((r) =>
    readJson<{ job_id: string }>(r, "twenty import"),
  );
