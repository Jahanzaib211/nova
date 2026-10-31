export type ModelSource = "config" | "runtime";

export interface Model {
  id: string;
  name: string;
  model: string;
  display_name?: string | null;
  description?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  supports_vision?: boolean;
  source?: ModelSource;
  use?: string | null;
  base_url?: string | null;
  has_api_key?: boolean;
  /** AMD-compute backing label (e.g. "AMD Instinct MI300X (Fireworks)"); null if not AMD-backed. */
  amd_compute?: string | null;
  /** Excluded from the default quick model picker; still fully usable via settings. */
  hidden?: boolean;
  /** Context window in tokens, as the serving runtime was actually launched. */
  max_input_tokens?: number | null;
}

export interface TokenUsageSettings {
  enabled: boolean;
}

export interface ModelsResponse {
  models: Model[];
  token_usage: TokenUsageSettings;
}

export interface ModelWriteRequest {
  name: string;
  model: string;
  use?: string;
  display_name?: string | null;
  description?: string | null;
  base_url?: string | null;
  /** Omit on update to keep the stored key. */
  api_key?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  supports_vision?: boolean;
  /** AMD-compute label for self-hosted AMD endpoints (Fireworks is auto-detected). */
  amd_compute?: string | null;
  /** Hide from the chat model picker; false surfaces the model in chat. */
  hidden?: boolean;
  /** Context window in tokens. Must match what the serving runtime was launched with. */
  max_input_tokens?: number | null;
}

export interface ModelWriteResponse {
  ok: boolean;
  model?: Model | null;
}

export interface ModelTestResponse {
  ok: boolean;
  message: string;
}

/**
 * Human-readable label for a model. Runtime-registered models (e.g. local
 * llama-bridge models) often have no display_name — never render an empty
 * label; fall back to the stable model name.
 */
export function getModelLabel(
  model: Pick<Model, "name" | "display_name">,
): string {
  return model.display_name?.trim() ? model.display_name.trim() : model.name;
}

/**
 * Compact context-window label ("1M", "205K", "4.1K"). `null` when the entry
 * never declared a window, so callers can omit the badge instead of printing
 * a misleading "0".
 */
export function formatContextWindow(
  tokens: number | null | undefined,
): string | null {
  if (typeof tokens !== "number" || !Number.isFinite(tokens) || tokens <= 0) {
    return null;
  }
  const round = (value: number) =>
    Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (tokens >= 1_000_000) {
    return `${round(tokens / 1_000_000)}M`;
  }
  if (tokens >= 1_000) {
    // Whole thousands: "205K" reads better than "204.8K".
    return `${Math.round(tokens / 1_000)}K`;
  }
  return String(tokens);
}

/**
 * The model a request should use: the saved choice while the menu still
 * offers it, otherwise the first offered model. A hidden model is one the menu
 * no longer offers (retired, or its account ran dry), so keeping a saved choice
 * of it would pin every request to it.
 *
 * `isAvailable` is the caller's notion of "offered": the chat picker passes the
 * user's per-browser visibility (see `core/models/visibility.ts`) so a model the
 * user surfaced — or the "show hidden models" toggle — can be selected and stay
 * selected. It defaults to the server flag, which keeps this function's
 * behaviour for every other caller.
 */
export function resolveSelectableModel<
  M extends Pick<Model, "name" | "hidden">,
>(
  models: readonly M[],
  savedName: unknown,
  isAvailable: (model: M) => boolean = (model) => !model.hidden,
): M | undefined {
  return (
    models.find((m) => m.name === savedName && isAvailable(m)) ??
    models.find((m) => isAvailable(m)) ??
    models[0]
  );
}
