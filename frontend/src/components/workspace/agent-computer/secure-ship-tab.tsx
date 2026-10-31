"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  LoaderCircleIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react";
import { useMemo } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import {
  useRunSecureShip,
  useSecureShipReport,
  useSecureShipToolkit,
  type SecureShipFinding,
  type SecureShipScanner,
  type SecureShipScannerStatus,
  type SecureShipSeverity,
  type SecureShipVerdict,
} from "@/core/sandbox/secure-ship";
import { cn } from "@/lib/utils";

// Tab: Secure-Ship — the pre-deploy security gate
// ──────────────────────────────────────────────────────────

/**
 * Verdict colours.
 *
 * The theme's semantic tokens are already the hues this panel needs:
 * `success` is emerald, `warning` is amber, `destructive` is red, and
 * `muted-foreground` is the neutral slate used for "the gate could not
 * decide". Using the tokens (rather than raw palette classes) keeps the
 * banner legible in both themes, like the rest of the panel.
 */
function verdictStyle(verdict: SecureShipVerdict) {
  switch (verdict) {
    case "pass":
      return {
        icon: CheckCircle2Icon,
        text: "text-success",
        border: "border-success/40",
        bg: "bg-success/10",
      };
    case "warn":
      return {
        icon: AlertTriangleIcon,
        text: "text-warning",
        border: "border-warning/40",
        bg: "bg-warning/10",
      };
    case "block":
      return {
        icon: XCircleIcon,
        text: "text-destructive",
        border: "border-destructive/50",
        bg: "bg-destructive/10",
      };
    default:
      return {
        icon: ShieldAlertIcon,
        text: "text-muted-foreground",
        border: "border-panel-border",
        bg: "bg-muted/10",
      };
  }
}

/** Same convention as the Review tab: destructive for the top tier, amber for
 *  the middle, muted for informational. */
function severityColor(severity: SecureShipSeverity): string {
  if (severity === "critical" || severity === "high") return "text-destructive";
  if (severity === "medium") return "text-warning";
  return "text-muted-foreground";
}

function scannerStatusStyle(status: SecureShipScannerStatus) {
  switch (status) {
    case "pass":
      return { text: "text-success", label: "pass" };
    case "fail":
      return { text: "text-destructive", label: "fail" };
    case "skipped":
      return { text: "text-muted-foreground/70", label: "skipped" };
    default:
      return { text: "text-warning", label: "error" };
  }
}

const SEVERITY_ORDER: SecureShipSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
];

function formatDuration(ms: number | undefined | null): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function SecureShipPanel({
  threadId,
  active = true,
}: {
  threadId: string;
  /** Gates the report poll — a hidden tab must not keep asking the gateway. */
  active?: boolean;
}) {
  const { t } = useI18n();
  const s = t.agentComputer.secureShip;
  const reportQuery = useSecureShipReport(threadId, active);
  const toolkitQuery = useSecureShipToolkit(active);
  const runGate = useRunSecureShip();

  const report = reportQuery.data ?? null;
  // The report crosses the network unchecked, so every collection access
  // below falls back rather than assuming the array is there.
  const scanners = report?.scanners ?? [];

  const grouped = useMemo(() => {
    // The findings array is read inside the memo (not hoisted to a variable
    // above) so the memo's dependency is the report itself — a fresh array
    // literal would otherwise invalidate it on every render.
    const findings = report?.findings ?? [];
    const bySeverity = new Map<SecureShipSeverity, SecureShipFinding[]>();
    for (const severity of SEVERITY_ORDER) bySeverity.set(severity, []);
    for (const finding of findings) {
      const bucket = bySeverity.get(finding.severity);
      if (bucket) bucket.push(finding);
      else bySeverity.get("info")?.push(finding);
    }
    return SEVERITY_ORDER.map((severity) => ({
      severity,
      items: bySeverity.get(severity) ?? [],
    })).filter((group) => group.items.length > 0);
  }, [report]);

  const toolkit = toolkitQuery.data;
  const toolkitGroups = useMemo(
    () =>
      Object.entries(toolkit?.groups ?? {})
        .map(([domain, tools]) => ({ domain, tools: tools ?? [] }))
        .sort((a, b) => b.tools.length - a.tools.length),
    [toolkit],
  );

  // A verdict we do not recognise is not a pass. The report is an unchecked
  // cast from the network, so an unknown value is reported as "error" — the
  // panel says "could not decide" rather than claiming a verdict that was
  // never reached.
  const rawVerdict = report?.verdict;
  const verdict: SecureShipVerdict | null = report
    ? rawVerdict === "pass" ||
      rawVerdict === "warn" ||
      rawVerdict === "block" ||
      rawVerdict === "error"
      ? rawVerdict
      : "error"
    : null;
  const style = verdict ? verdictStyle(verdict) : null;
  const VerdictIcon = style?.icon ?? ShieldCheckIcon;
  const isBlocked = verdict === "block";
  // Uppercased in the DOM, not just by CSS: `text-transform` is a visual
  // hint, and the verdict word is the one thing on this panel that has to
  // read as a verdict to a screen reader and to a test.
  const verdictLabel = verdict ? verdict.toUpperCase() : "";

  const handleRun = () => {
    runGate.mutate({ thread_id: threadId });
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header: what this tab is, and the one action it owns. */}
      <div className="border-panel-border bg-muted/20 flex shrink-0 items-center justify-between border-b px-2 py-1">
        <span className="text-muted-foreground/70 font-mono text-xs">
          {s.title}
        </span>
        <button
          type="button"
          onClick={handleRun}
          disabled={runGate.isPending}
          aria-label={runGate.isPending ? s.running : s.runGate}
          className="border-panel-border bg-background/60 hover:bg-muted/40 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-40"
        >
          {runGate.isPending ? (
            <LoaderCircleIcon className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <ShieldCheckIcon className="h-3 w-3" aria-hidden />
          )}
          {runGate.isPending ? s.running : s.runGate}
        </button>
      </div>

      <ScrollArea className="h-full">
        <div className="flex flex-col gap-3 p-3 text-xs">
          {/* ── Verdict banner ── */}
          {report && style ? (
            <div
              role="status"
              aria-live="polite"
              data-verdict={verdict}
              data-testid="secure-ship-verdict"
              className={cn("rounded-lg border p-3", style.border, style.bg)}
            >
              <div className={cn("flex items-center gap-2", style.text)}>
                <VerdictIcon className="h-5 w-5 shrink-0" aria-hidden />
                <span className="text-sm font-semibold tracking-wide uppercase">
                  {verdictLabel}
                </span>
                <span className="ml-auto font-mono text-[10px] opacity-80">
                  {formatDuration(report.duration_ms)}
                </span>
              </div>
              <p className={cn("mt-1 text-[11px]", style.text)}>
                {isBlocked ? s.blocked : s.verdictHint[verdict ?? "error"]}
              </p>
              {report.reason ? (
                <p className="text-muted-foreground/90 mt-1 text-[11px]">
                  {report.reason}
                </p>
              ) : null}
              {isBlocked ? (
                <p className="text-destructive mt-2 text-[11px] font-medium">
                  {s.deployBlocked}
                </p>
              ) : null}
            </div>
          ) : reportQuery.isError ? (
            <div
              role="alert"
              className="border-destructive/40 bg-destructive/10 rounded-lg border p-3"
            >
              <div className="text-destructive text-sm font-medium">
                {s.loadFailed}
              </div>
              <p className="text-muted-foreground/80 mt-1 text-[11px]">
                {s.loadFailedHint}
              </p>
              <button
                type="button"
                onClick={() => void reportQuery.refetch()}
                className="text-muted-foreground/70 hover:text-foreground mt-1 text-[11px] underline underline-offset-2"
              >
                {s.retry}
              </button>
            </div>
          ) : reportQuery.isPending ? (
            <div className="border-panel-border bg-muted/10 rounded-lg border p-3">
              <div className="text-muted-foreground flex items-center gap-2 text-sm font-medium">
                <LoaderCircleIcon
                  className="h-3 w-3 animate-spin"
                  aria-hidden
                />
                {s.loading}
              </div>
            </div>
          ) : (
            <div className="border-panel-border bg-muted/10 rounded-lg border p-3">
              <div className="text-muted-foreground text-sm font-medium">
                {s.emptyTitle}
              </div>
              <p className="text-muted-foreground/70 mt-1 text-[11px]">
                {s.emptyHint}
              </p>
            </div>
          )}

          {/* A failed scan is a result worth showing: the panel exists to
              surface breakage, so the error is not swallowed. */}
          {runGate.isError ? (
            <div
              role="alert"
              className="border-destructive/40 bg-destructive/10 rounded-lg border p-2"
            >
              <div className="text-destructive text-[11px] font-medium">
                {s.runFailed}
              </div>
              <p className="text-muted-foreground/80 mt-0.5 text-[11px]">
                {runGate.error instanceof Error
                  ? runGate.error.message
                  : s.runFailedHint}
              </p>
            </div>
          ) : null}

          {/* ── Scanners ── */}
          {scanners.length > 0 ? (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {s.scanners}
              </div>
              <div className="flex flex-col gap-1">
                {scanners.map((scanner: SecureShipScanner) => {
                  const status = scannerStatusStyle(scanner.status);
                  return (
                    <div
                      key={`${scanner.scanner}:${scanner.status}`}
                      className="border-panel-border bg-muted/10 rounded border px-2 py-1"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            "font-mono text-[10px] uppercase",
                            status.text,
                          )}
                        >
                          {status.label}
                        </span>
                        <span className="text-muted-foreground/90 font-mono text-[11px]">
                          {scanner.scanner}
                        </span>
                        <span className="text-muted-foreground/40 ml-auto font-mono text-[10px]">
                          {formatDuration(scanner.duration_ms)}
                        </span>
                      </div>
                      {scanner.summary ? (
                        <div className="text-muted-foreground/70 mt-0.5 text-[11px]">
                          {scanner.summary}
                        </div>
                      ) : null}
                      {scanner.status === "skipped" ? (
                        <div className="text-muted-foreground/50 mt-0.5 text-[10px]">
                          {s.scannerUnavailable}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* ── Findings, grouped by severity ── */}
          {grouped.length > 0 ? (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {s.findings}
              </div>
              <div className="flex flex-col gap-2">
                {grouped.map((group) => (
                  <div key={group.severity} className="flex flex-col gap-1">
                    <div
                      className={cn(
                        "font-mono text-[10px] uppercase",
                        severityColor(group.severity),
                      )}
                    >
                      {s.severity[group.severity]} · {group.items.length}
                    </div>
                    {group.items.map((finding, index) => (
                      <div
                        key={`${finding.scanner}:${finding.title}:${index}`}
                        className="border-panel-border bg-muted/10 rounded border px-2 py-1"
                      >
                        <div className="text-muted-foreground/90">
                          {finding.title}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[10px]">
                          {finding.path ? (
                            <span className="text-muted-foreground/70 truncate">
                              {finding.path}
                              {finding.line ? `:${finding.line}` : ""}
                            </span>
                          ) : null}
                          <span className="text-muted-foreground/40">
                            {finding.scanner}
                          </span>
                          {finding.rule ? (
                            <span className="text-muted-foreground/40">
                              {finding.rule}
                            </span>
                          ) : null}
                        </div>
                        {finding.detail ? (
                          <div className="text-muted-foreground/70 mt-0.5 text-[11px]">
                            {finding.detail}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : report ? (
            <div className="text-muted-foreground/70 text-[11px]">
              {s.noFindings}
            </div>
          ) : null}

          {/* ── Security toolkit ──
              The 40 security tools every sandbox ships were advertised on
              the landing page and invisible in the product itself. This is
              where they finally show up. */}
          <div>
            <div className="text-muted-foreground/70 mb-1 font-medium">
              {s.toolkitTitle}
              {typeof toolkit?.total === "number" ? (
                <span className="text-muted-foreground/50 ml-1 font-mono text-[10px]">
                  {s.toolkitTotal(toolkit.total)}
                </span>
              ) : null}
            </div>
            {toolkitGroups.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                {toolkitGroups.map((group) => (
                  <div key={group.domain}>
                    <div className="text-muted-foreground/60 font-mono text-[10px] uppercase">
                      {group.domain}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {group.tools.map((tool) => (
                        <span
                          key={`${group.domain}:${tool}`}
                          className="border-panel-border bg-muted/10 text-muted-foreground/70 rounded border px-1.5 py-0.5 font-mono text-[10px]"
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : toolkitQuery.isError ? (
              <div className="text-muted-foreground/60 text-[11px]">
                {s.toolkitUnavailable}
              </div>
            ) : (
              <div className="text-muted-foreground/60 text-[11px]">
                {s.toolkitLoading}
              </div>
            )}
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}
