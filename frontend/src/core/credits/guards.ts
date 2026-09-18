/**
 * Runtime validation for the Account section's payloads. The meter and the
 * referral card call `.toLocaleString()` on these numbers in render, so a
 * mis-shaped response must be rejected here, not discovered as a TypeError
 * that unmounts the whole settings route.
 */
import type { Credits, Referral } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const isFiniteNumber = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);

export function isCredits(value: unknown): value is Credits {
  return (
    isRecord(value) &&
    typeof value.plan === "string" &&
    isFiniteNumber(value.daily_limit) &&
    isFiniteNumber(value.used) &&
    isFiniteNumber(value.remaining) &&
    typeof value.unlimited === "boolean" &&
    (value.request_status === null || typeof value.request_status === "string")
  );
}

export function isReferral(value: unknown): value is Referral {
  return (
    isRecord(value) &&
    typeof value.code === "string" &&
    isFiniteNumber(value.referral_count) &&
    isFiniteNumber(value.bonus_daily_tokens)
  );
}
