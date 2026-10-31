import type { FeatureFlags } from "@/core/runtime/feature-flags";

/**
 * What the email marketing surfaces may render.
 *
 * - "loading"  the server has not answered /api/runtime/capabilities yet.
 *              Until it does every flag reads as off, so saying "not
 *              enabled" now would be a claim the next tick contradicts.
 * - "enabled"  the server advertises email_marketing, i.e. config.yaml has
 *              email_marketing.enabled: true and /api/em is mounted.
 * - "disabled" the feature is off on this deployment: the API is not there,
 *              so every panel would be an error page.
 *
 * This is the same rule the rest of the app uses for a feature-gated
 * surface: the server-declared flag wins, the local env override can only
 * add (see mergeFeatureFlags), and a flag the server did not advertise
 * counts as off.
 */
export type EmailGate = "loading" | "enabled" | "disabled";

/** The one flag that gates the email marketing UI. */
export type EmailGateFlags = Pick<FeatureFlags, "email_marketing">;

export function emailGate(flags: EmailGateFlags, isLoading = false): EmailGate {
  if (isLoading) return "loading";
  return flags.email_marketing ? "enabled" : "disabled";
}
