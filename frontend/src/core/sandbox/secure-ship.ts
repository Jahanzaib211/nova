"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invoke } from "@/core/capabilities/api";

import { useSandboxThreadId } from "./live";

/**
 * Secure-Ship — the pre-deploy security gate.
 *
 * The gate runs the sandbox's security scanners over the workspace and
 * returns a verdict (`pass` / `warn` / `block` / `error`) plus the findings
 * behind it. Everything the panel renders comes from these capability ops:
 *
 *   - `secure_ship.report`  — the last gate report for a thread (read)
 *   - `secure_ship.toolkit` — the security tools the sandbox ships (read)
 *   - `secure_ship.scan`    — run the gate now (execute)
 *
 * The report payload crosses the network as an unchecked cast, so every
 * field access in the panel has to survive a partial body — the panel reads
 * defensively rather than trusting the shape.
 */

// ── Types ────────────────────────────────────────────────────

export type SecureShipVerdict = "pass" | "warn" | "block" | "error";

export type SecureShipSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info";

export type SecureShipScannerStatus = "pass" | "fail" | "skipped" | "error";

export type SecureShipFinding = {
  scanner: string;
  severity: SecureShipSeverity;
  title: string;
  path: string;
  /** 1-based line number; `null` when the finding is file-scoped. */
  line: number | null;
  detail: string;
  rule: string;
};

export type SecureShipScanner = {
  scanner: string;
  status: SecureShipScannerStatus;
  summary: string;
  findings: SecureShipFinding[];
  duration_ms: number;
};

export type SecureShipPolicy = {
  name?: string;
  version?: string | null;
  [key: string]: unknown;
};

export type SecureShipReport = {
  thread_id: string;
  verdict: SecureShipVerdict;
  policy: SecureShipPolicy;
  scanners: SecureShipScanner[];
  findings: SecureShipFinding[];
  /** Findings per severity. A missing key means "none of that severity". */
  counts: Record<SecureShipSeverity, number>;
  duration_ms: number;
  generated_at: string;
  reason: string;
};

export type SecureShipToolkit = {
  total: number;
  /** Tool names grouped by domain (e.g. `secrets`, `web`, `supply-chain`). */
  groups: Record<string, string[]>;
  version?: string | null;
};

export type SecureShipScanInput = {
  thread_id?: string;
  /** Deeper scan — slower, but covers more of the tree. */
  deep?: boolean;
};

// ── Capability invocation ────────────────────────────────────

/**
 * Op names for the Secure-Ship module.
 *
 * `core/capabilities/generated.ts` is GENERATED from the backend contract
 * and is regenerated when the backend module ships; until that lands the
 * names are not yet part of `CapabilityOpName`, so they are narrowed once
 * here rather than at every call site. The casts below become no-ops the
 * moment the regenerated contract carries these ops.
 *
 * `invoke` also rejects an op that is missing from `OP_META` ("unknown
 * operation"), so until the regenerated contract lands these calls fail
 * loudly rather than silently hitting a 404 — which is the right failure
 * for a missing capability, and the reason the panel has a real error state
 * rather than an eternal "no report yet".
 */
export type SecureShipOpName =
  | "secure_ship.scan"
  | "secure_ship.report"
  | "secure_ship.toolkit";

/**
 * `invoke` is generic over the generated op contract, and the generated
 * contract does not carry the `secure_ship.*` ops until the backend module
 * ships. The call is therefore narrowed through ONE local cast here rather
 * than scattering `as never` through the file; when the regenerated contract
 * carries these ops, this alias is the only line that has to change.
 */
const invokeSecureShip = invoke as unknown as <TOutput>(
  op: SecureShipOpName,
  input: unknown,
) => Promise<TOutput>;

// ── Query keys & timings ─────────────────────────────────────

/**
 * The gate report is keyed by thread, like the rest of the sandbox surface:
 * `["sandbox", "secure-ship", threadId]`. The toolkit is not thread-scoped
 * and lives under its own key so a report invalidation never refetches it.
 */
export const SECURE_SHIP_REPORT_KEY = "secure-ship";
export const SECURE_SHIP_TOOLKIT_KEY = "secure-ship-toolkit";

const TIMING = {
  /**
   * How often the report is re-read while the panel is open. The gate can
   * also be started by the agent (not only from this panel), so the panel
   * polls — but only while its tab is active, and only while the thread
   * exists server-side.
   */
  POLL_MS: 10_000,
  /** The tool list changes when the sandbox image does — rare. */
  TOOLKIT_STALE_MS: 300_000,
} as const;

// ── useSecureShipReport ──────────────────────────────────────

/**
 * The last gate report for a thread, or `null` when no gate has run.
 *
 * `enabled` gates the query, so a hidden tab does not poll. The panel passes
 * its `active` flag; an unsaved thread has no report to fetch at all.
 */
export function useSecureShipReport(
  requestedThreadId: string | null,
  enabled = true,
) {
  const threadId = useSandboxThreadId(requestedThreadId);
  const query = useQuery<SecureShipReport | null>({
    queryKey: ["sandbox", SECURE_SHIP_REPORT_KEY, threadId],
    queryFn: async () => {
      const out = await invokeSecureShip<{ report: SecureShipReport | null }>(
        "secure_ship.report",
        { thread_id: threadId ?? undefined },
      );
      return out?.report ?? null;
    },
    enabled: Boolean(threadId) && enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
    refetchInterval: enabled ? TIMING.POLL_MS : false,
  });
  // "No report yet" and "the report says there is nothing" are different
  // states; normalise the first to null so the panel can branch on it.
  return { ...query, data: query.data ?? null };
}

// ── useSecureShipToolkit ─────────────────────────────────────

/**
 * The security tools every sandbox ships, grouped by domain. This is the
 * list the landing page advertises (40 tools) — it was never visible in the
 * product itself.
 */
export function useSecureShipToolkit(enabled = true) {
  const query = useQuery<SecureShipToolkit | null>({
    queryKey: ["sandbox", SECURE_SHIP_TOOLKIT_KEY],
    queryFn: async () => {
      const out = await invokeSecureShip<{
        toolkit: SecureShipToolkit | null;
      }>("secure_ship.toolkit", {});
      return out?.toolkit ?? null;
    },
    enabled,
    staleTime: TIMING.TOOLKIT_STALE_MS,
    refetchOnWindowFocus: false,
  });
  return { ...query, data: query.data ?? null };
}

// ── useRunSecureShip ─────────────────────────────────────────

/**
 * Run the gate now.
 *
 * The scan is an `execute` op: it can take a while, so it is a mutation
 * rather than a query, and its result lands in the report cache — which is
 * why a successful run invalidates the report for every thread rather than
 * only the one that was scanned.
 */
export function useRunSecureShip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: SecureShipScanInput = {}) => {
      const out = await invokeSecureShip<{ report: SecureShipReport | null }>(
        "secure_ship.scan",
        input,
      );
      return out?.report ?? null;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["sandbox", SECURE_SHIP_REPORT_KEY],
      });
    },
  });
}
