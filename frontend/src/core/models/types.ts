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
