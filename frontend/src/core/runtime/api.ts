/** REST API for /api/runtime/capabilities.
 *
 * Uses the shared fetch() helper so credentials (HttpOnly access_token
 * cookie) and CSRF (for any future state-changing calls) are handled
 * uniformly with the rest of the codebase.
 */
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { CapabilitiesResponse } from "./types";

export async function loadCapabilities(
  threadId?: string,
): Promise<CapabilitiesResponse> {
  // Scoping the circuit snapshot to one thread keeps the runtime bar honest:
  // it sits above a single chat's Agent's Computer, so a breaker opened by a
  // different thread must not label this chat degraded.
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/runtime/capabilities${query}`,
  );
  if (!res.ok) {
    throw new Error(
      `Failed to load capabilities: HTTP ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as CapabilitiesResponse;
}
