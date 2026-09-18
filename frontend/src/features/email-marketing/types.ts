/**
 * Email-marketing vocabulary.
 *
 * Pinned to `contracts/email_marketing_events_contract.json` by
 * `tests/unit/contracts/email-marketing-events.contract.test.ts`; the
 * backend (`deerflow.email_marketing.events`) pins the same file.
 */

export const EMAIL_EVENT_TYPES = [
  "queued",
  "sent",
  "deferred",
  "bounced_hard",
  "bounced_soft",
  "complained",
  "opened",
  "clicked",
  "unsubscribed",
  "suppressed",
  "failed",
] as const;
export type EmailEventType = (typeof EMAIL_EVENT_TYPES)[number];

export const CAMPAIGN_STATUSES = [
  "draft",
  "scheduled",
  "sending",
  "paused",
  "completed",
  "cancelled",
  "failed",
] as const;
export type CampaignStatus = (typeof CAMPAIGN_STATUSES)[number];

export const TERMINAL_CAMPAIGN_STATUSES: readonly CampaignStatus[] = [
  "completed",
  "cancelled",
  "failed",
];

export const CONTACT_STATUSES = [
  "pending",
  "subscribed",
  "unsubscribed",
  "bounced",
  "complained",
] as const;
export type ContactStatus = (typeof CONTACT_STATUSES)[number];

export const SUPPRESSION_REASONS = [
  "hard_bounce",
  "complaint",
  "unsubscribe",
  "manual",
  "invalid",
] as const;
export type SuppressionReason = (typeof SUPPRESSION_REASONS)[number];
