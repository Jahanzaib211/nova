/** REST client for /api/integrations. Shapes mirror routers/integrations.py. */
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { type IntegrationHealthItem, isIntegrationHealthItem } from "./types";

export interface IntegrationsList {
  enabled: boolean;
  probe_cache_seconds: number;
  integrations: IntegrationHealthItem[];
}

function url(path: string): string {
  return `${getBackendBaseURL()}${path}`;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function listIntegrations(
  refresh = false,
): Promise<IntegrationsList> {
  const res = await fetch(
    url(`/api/integrations${refresh ? "?refresh=1" : ""}`),
  );
  const body = await json<IntegrationsList>(res);
  return {
    enabled: Boolean(body.enabled),
    probe_cache_seconds: Number(body.probe_cache_seconds ?? 0),
    // Drop anything off-contract instead of rendering a half-shaped card.
    integrations: (body.integrations ?? []).filter(isIntegrationHealthItem),
  };
}

export async function probeIntegration(
  id: string,
): Promise<IntegrationHealthItem> {
  const res = await fetch(
    url(`/api/integrations/${encodeURIComponent(id)}/probe`),
    {
      method: "POST",
      headers: getCsrfHeaders(),
    },
  );
  const item = await json<unknown>(res);
  if (!isIntegrationHealthItem(item)) throw new Error("Malformed integration");
  return item;
}
