"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCapability } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/core/runtime/feature-flags";
import { cn } from "@/lib/utils";

export interface RuntimeChoice {
  runtime?: string;
  runtime_account?: string;
  permission_mode?: "full" | "standard" | "plan";
}

export interface RuntimeOption {
  id: string;
  label: string;
  kind: string;
  binary_on_path: boolean;
  accounts: Array<{ id: string; label: string; available: boolean }>;
}

const MODES = ["full", "standard", "plan"] as const;

/** The runtime list the picker and the trigger label both read from. */
export function useRuntimeOptions() {
  const flags = useFeatureFlags();
  const query = useCapability("runtimes.list", {}, { enabled: flags.runtimes });
  const runtimes = (query.data?.runtimes ?? []) as unknown as RuntimeOption[];
  return {
    enabled: flags.runtimes && Boolean(query.data?.enabled),
    runtimes,
    defaultRuntime: query.data?.default ?? "native",
  };
}

/** Whether `runtime` names an ACP runtime (the model is then the runtime's own). */
export function isAcpRuntime(
  runtime: string | undefined,
  runtimes: RuntimeOption[],
) {
  return Boolean(
    runtime &&
    runtime !== "native" &&
    runtimes.find((r) => r.id === runtime)?.kind === "acp",
  );
}

/** What the model button should say: the runtime when it owns the model, the model otherwise. */
export function runtimeTriggerLabel(
  runtime: string | undefined,
  runtimes: RuntimeOption[],
  value: RuntimeChoice,
  autoLabel: string,
): { title: string; subtitle: string } | null {
  const rt = runtimes.find((r) => r.id === runtime);
  if (rt?.kind !== "acp") return null;
  const account =
    value.runtime_account && value.runtime_account !== "auto"
      ? rt.accounts.find((a) => a.id === value.runtime_account)
      : rt.accounts.find((a) => a.available);
  return { title: rt.label, subtitle: account?.label ?? autoLabel };
}

/**
 * Per-chat runtime controls: which runtime runs the chat (Nova / Claude Code /
 * OpenClaw), which account, and the permission preset. Runtime comes first —
 * when an ACP runtime is chosen it owns the model, so the model list is the
 * runtime's concern, not this menu's. Values ride the run context like
 * model_name.
 */
export function RuntimePicker({
  value,
  onChange,
  className,
}: {
  value: RuntimeChoice;
  onChange: (next: RuntimeChoice) => void;
  className?: string;
}) {
  const { t } = useI18n();
  const s = t.features.runtimePicker;
  const { enabled, runtimes, defaultRuntime } = useRuntimeOptions();
  if (runtimes.length === 0) return null;

  const runtime = value.runtime ?? defaultRuntime;
  const current = runtimes.find((r) => r.id === runtime);
  const isAcp = current?.kind === "acp";
  const ready = (r: RuntimeOption) =>
    r.kind === "native" ||
    (r.binary_on_path && r.accounts.some((a) => a.available));

  return (
    <div
      className={cn("space-y-2 p-2 text-xs", className)}
      data-testid="runtime-picker"
    >
      {/* pr-8: the dialog's close button is absolutely positioned top-right. */}
      <div className="text-muted-foreground px-1 pr-8 text-[11px] font-semibold tracking-wider uppercase">
        {s.runtime}
      </div>
      {!enabled ? (
        <p className="text-muted-foreground px-1">{s.disabled}</p>
      ) : null}
      <Select
        value={runtime}
        onValueChange={(v) =>
          onChange({ ...value, runtime: v, runtime_account: "auto" })
        }
        disabled={!enabled}
      >
        <SelectTrigger
          size="sm"
          className="w-[calc(100%-2rem)]"
          aria-label={s.runtime}
          data-testid="runtime-select"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {runtimes.map((r) => (
            <SelectItem key={r.id} value={r.id} disabled={!ready(r)}>
              <span className="inline-flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className={cn(
                    "size-1.5 rounded-full",
                    ready(r) ? "bg-success" : "bg-muted-foreground/40",
                  )}
                />
                {r.id === "native" ? s.runtimeNative : r.label}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {isAcp ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-muted-foreground">{s.account}</span>
            <Select
              value={value.runtime_account ?? "auto"}
              onValueChange={(v) => onChange({ ...value, runtime_account: v })}
            >
              <SelectTrigger
                size="sm"
                className="w-full"
                aria-label={s.account}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{s.accountAuto}</SelectItem>
                {(current?.accounts ?? []).map((a) => (
                  <SelectItem key={a.id} value={a.id} disabled={!a.available}>
                    {a.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-muted-foreground">{s.permission}</span>
            <Select
              value={value.permission_mode ?? "standard"}
              onValueChange={(v) =>
                onChange({
                  ...value,
                  permission_mode: v as RuntimeChoice["permission_mode"],
                })
              }
            >
              <SelectTrigger
                size="sm"
                className="w-full"
                aria-label={s.permission}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODES.map((m) => (
                  <SelectItem key={m} value={m}>
                    {s.modes[m]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="text-muted-foreground">
          {isAcp ? s.modelOwnedByRuntime : s.appliesToThisChat}
        </span>
        {value.runtime ? (
          <button
            type="button"
            className="text-primary shrink-0 underline-offset-2 hover:underline"
            onClick={() =>
              onChange({
                runtime: undefined,
                runtime_account: undefined,
                permission_mode: undefined,
              })
            }
          >
            {s.reset}
          </button>
        ) : null}
      </div>
    </div>
  );
}
