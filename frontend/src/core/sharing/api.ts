import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface ShareLink {
  token: string;
  thread_id: string;
  shared: boolean;
}

export interface SharedMessage {
  event_type: string;
  content: unknown;
  created_at: string | null;
  seq: number;
  run_id: string;
}

export interface SharedThreadView {
  token: string;
  thread_id: string;
  thread_title: string | null;
  created_at: string;
  messages: SharedMessage[];
}

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  return typeof error.detail === "string" ? error.detail : fallback;
}

export async function createShareLink(threadId: string): Promise<ShareLink> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/share`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to create share link"),
    );
  }
  return (await response.json()) as ShareLink;
}

export async function revokeShareLink(threadId: string): Promise<ShareLink> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/share`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to revoke share link"),
    );
  }
  return (await response.json()) as ShareLink;
}

export async function loadSharedThread(
  token: string,
): Promise<SharedThreadView> {
  const response = await fetch(`${getBackendBaseURL()}/api/share/${token}`);
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Shared thread not found or revoked"),
    );
  }
  return (await response.json()) as SharedThreadView;
}
