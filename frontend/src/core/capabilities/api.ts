/**
 * Typed client for `/api/capabilities/ops`.
 *
 * `invoke("jobs.list", { limit: 10 })` is checked against the generated
 * contract: the op name must exist and the input/output types come from the
 * backend's pydantic models (see generated.ts). Every settings page that is
 * "a view over a capability" uses this instead of a bespoke fetch, so the
 * UI cannot call something the harness and the MCP server do not also have.
 */
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import {
  type CapabilityOpName,
  type CapabilityOps,
  OP_META,
} from "./generated";

export interface ModuleStatus {
  configured: boolean;
  healthy: boolean;
  detail: string | null;
}

export interface OpsListing {
  snapshot: {
    version: number;
    modules: Array<{
      id: string;
      title: string;
      flag: string | null;
      config_key: string | null;
      description: string;
      operations: string[];
    }>;
    operations: Array<{
      name: string;
      module: string;
      kind: string;
      description: string;
      flag: string | null;
      harness: boolean;
      mcp: boolean;
      admin_only: boolean;
    }>;
  };
  status: Record<string, ModuleStatus>;
  flags: Record<string, boolean>;
}

export class CapabilityError extends Error {
  constructor(
    readonly op: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${op}: ${detail}`);
    this.name = "CapabilityError";
  }
}

function url(path: string): string {
  return `${getBackendBaseURL()}${path}`;
}

async function readDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail) return JSON.stringify(body.detail);
  } catch {
    // not JSON — fall through
  }
  return text || `HTTP ${res.status}`;
}

export async function listOps(): Promise<OpsListing> {
  const res = await fetch(url("/api/capabilities/ops"));
  if (!res.ok)
    throw new CapabilityError("ops", res.status, await readDetail(res));
  return (await res.json()) as OpsListing;
}

export async function invoke<N extends CapabilityOpName>(
  name: N,
  input: CapabilityOps[N]["input"],
): Promise<CapabilityOps[N]["output"]> {
  if (!(name in OP_META)) {
    throw new CapabilityError(name, 0, "unknown operation");
  }
  const res = await fetch(
    url(`/api/capabilities/ops/${encodeURIComponent(name)}`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
      body: JSON.stringify(input ?? {}),
    },
  );
  if (!res.ok)
    throw new CapabilityError(name, res.status, await readDetail(res));
  const body = (await res.json()) as { result: CapabilityOps[N]["output"] };
  return body.result;
}

export { OP_META };
export type { CapabilityOpName, CapabilityOps };
