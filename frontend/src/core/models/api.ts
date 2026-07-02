import { fetch } from "@/core/api/fetcher";

import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";

import type {
  ModelsResponse,
  ModelTestResponse,
  ModelWriteRequest,
  ModelWriteResponse,
} from "./types";

const STATIC_MODELS_RESPONSE: ModelsResponse = {
  models: [],
  token_usage: { enabled: false },
};

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body.detail) {
      return body.detail;
    }
  } catch {
    // non-JSON error body — fall through to the status line
  }
  return `HTTP ${res.status}`;
}

export async function loadModels(): Promise<ModelsResponse> {
  if (isStaticWebsiteOnly()) {
    return STATIC_MODELS_RESPONSE;
  }

  const res = await fetch(`${getBackendBaseURL()}/api/models`);
  if (!res.ok) {
    throw new Error(`Failed to load models: HTTP ${res.status}`);
  }
  const data = (await res.json()) as Partial<ModelsResponse>;
  return {
    models: data.models ?? [],
    token_usage: data.token_usage ?? { enabled: false },
  };
}

export async function createModel(
  request: ModelWriteRequest,
): Promise<ModelWriteResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return (await res.json()) as ModelWriteResponse;
}

export async function updateModel(
  name: string,
  request: ModelWriteRequest,
): Promise<ModelWriteResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/models/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return (await res.json()) as ModelWriteResponse;
}

export async function deleteModel(name: string): Promise<ModelWriteResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/models/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return (await res.json()) as ModelWriteResponse;
}

export async function testModel(name: string): Promise<ModelTestResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/models/${encodeURIComponent(name)}/test`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return (await res.json()) as ModelTestResponse;
}
