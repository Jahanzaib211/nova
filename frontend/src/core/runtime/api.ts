/** REST API for /api/runtime/capabilities.
 *
 * Uses the shared fetch() helper so credentials (HttpOnly access_token
 * cookie) and CSRF (for any future state-changing calls) are handled
 * uniformly with the rest of the codebase.
 */
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { CapabilitiesResponse } from "./types";

export async function loadCapabilities(): Promise<CapabilitiesResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/runtime/capabilities`);
  if (!res.ok) {
    throw new Error(
      `Failed to load capabilities: HTTP ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as CapabilitiesResponse;
}