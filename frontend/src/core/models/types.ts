export type ModelSource = "config" | "runtime";

export interface Model {
  id: string;
  name: string;
  model: string;
  display_name: string;
  description?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  supports_vision?: boolean;
  source?: ModelSource;
  use?: string | null;
  base_url?: string | null;
  has_api_key?: boolean;
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
}

export interface ModelWriteResponse {
  ok: boolean;
  model?: Model | null;
}

export interface ModelTestResponse {
  ok: boolean;
  message: string;
}
