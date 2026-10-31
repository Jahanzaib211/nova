import type { FeatureFlags } from "@/core/runtime/feature-flags";

export type RuntimesGate = "loading" | "disabled" | "enabled";

/**
 * Whether the Runtimes section may claim that runtimes are switched off.
 *
 * Every feature flag reads as off until `/api/runtime/capabilities`
 * answers, so a page that renders "runtimes.enabled is false in
 * config.yaml" during the request states something the next tick
 * contradicts. The gate is the same rule the email page follows
 * (`features/email-marketing/gate.ts`): loading, disabled, enabled.
 */
export function runtimesGate(
  flags: Pick<FeatureFlags, "runtimes">,
  isLoading = false,
): RuntimesGate {
  if (isLoading) return "loading";
  return flags.runtimes ? "enabled" : "disabled";
}
