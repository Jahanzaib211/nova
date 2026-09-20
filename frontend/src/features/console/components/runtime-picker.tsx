"use client";

import { useCapability } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/core/runtime/feature-flags";

export interface RuntimeChoice {
  runtime?: string;
  runtime_account?: string;
  permission_mode?: "full" | "standard" | "plan";
}

const MODES = ["full", "standard", "plan"] as const;

/**
 * Per-chat runtime controls under the model list: which runtime runs the
 * chat (Nova / Claude Code / OpenClaw), which account, and the permission
 * preset — the "Account for this chat" + permission chip pattern. Values
 * ride along the run context exactly like model_name.
 */
export function RuntimePicker({
  value,
  onChange,
}: {
  value: RuntimeChoice;
  onChange: (next: RuntimeChoice) => void;
}) {
  const { t } = useI18n();
  const s = t.features.runtimePicker;
  const flags = useFeatureFlags();
  const runtimes = useCapability(
    "runtimes.list",
    {},
    { enabled: flags.runtimes },
  );

  if (!flags.runtimes) return null;
  const list = (runtimes.data?.runtimes ?? []) as Array<{
    id: string;
    label: string;
    kind: string;
    accounts: Array<{ id: string; label: string; available: boolean }>;
  }>;
  const runtime = value.runtime ?? runtimes.data?.default ?? "native";
  const current = list.find((r) => r.id === runtime);
  const isAcp = current?.kind === "acp";
  const selectClass =
    "bg-background w-full rounded-md border px-2 py-1 text-xs";

  return (
    <div
      className="space-y-2 border-t p-2 text-xs"
      data-testid="runtime-picker"
    >
      {!runtimes.data?.enabled ? (
        <p className="text-muted-foreground">{s.disabled}</p>
      ) : null}
      <label className="flex flex-col gap-1">
        <span className="text-muted-foreground">{s.runtime}</span>
        <select
          className={selectClass}
          value={runtime}
          onChange={(e) =>
            onChange({
              ...value,
              runtime: e.target.value,
              runtime_account: "auto",
            })
          }
          aria-label={s.runtime}
        >
          {list.map((r) => (
            <option key={r.id} value={r.id}>
              {r.id === "native" ? s.runtimeNative : r.label}
            </option>
          ))}
        </select>
      </label>
      {isAcp ? (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-muted-foreground">{s.account}</span>
            <select
              className={selectClass}
              value={value.runtime_account ?? "auto"}
              onChange={(e) =>
                onChange({ ...value, runtime_account: e.target.value })
              }
              aria-label={s.account}
            >
              <option value="auto">{s.accountAuto}</option>
              {(current?.accounts ?? []).map((a) => (
                <option key={a.id} value={a.id} disabled={!a.available}>
                  {a.label}
                  {a.available ? "" : " — unavailable"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-muted-foreground">{s.permission}</span>
            <select
              className={selectClass}
              value={value.permission_mode ?? "standard"}
              onChange={(e) =>
                onChange({
                  ...value,
                  permission_mode: e.target
                    .value as RuntimeChoice["permission_mode"],
                })
              }
              aria-label={s.permission}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {s.modes[m]}
                </option>
              ))}
            </select>
          </label>
        </>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">{s.appliesToThisChat}</span>
        <button
          type="button"
          className="text-primary underline-offset-2 hover:underline"
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
      </div>
    </div>
  );
}
