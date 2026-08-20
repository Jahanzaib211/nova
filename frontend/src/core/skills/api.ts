import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { Skill } from "./type";

export class SkillUpdateError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "SkillUpdateError";
    this.status = status;
  }
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

export async function loadSkills(): Promise<Skill[]> {
  const skills = await fetch(`${getBackendBaseURL()}/api/skills`);
  // TanStack Query requires queryFn to return a defined value. Surface
  // a graceful empty list on non-OK (401 during re-auth, 5xx during
  // gateway outage) so the hook's `data ?? []` is no longer needed and
  // the console stays clean.
  if (!skills.ok) {
    return [];
  }
  const json = (await skills.json()) as { skills?: Skill[] };
  return Array.isArray(json.skills) ? json.skills : [];
}

export async function enableSkill(skillName: string, enabled: boolean) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
  );
  if (!response.ok) {
    // Previously returned response.json() unconditionally here — a failed
    // toggle (403 admin-required, 404, 5xx) still resolved as "success",
    // so useEnableSkill's onSuccess fired, the query got invalidated, and
    // the Switch silently reverted with zero user feedback.
    throw new SkillUpdateError(
      response.status,
      await readErrorDetail(response, "Failed to update skill"),
    );
  }
  return response.json();
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    // Handle HTTP error responses (4xx, 5xx)
    const errorData = await response.json().catch(() => ({}));
    const errorMessage =
      errorData.detail ?? `HTTP ${response.status}: ${response.statusText}`;
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}

export interface CustomSkillContent extends Skill {
  content: string;
}

export interface CustomSkillHistoryEntry {
  ts?: string;
  action?: string;
  author?: string;
  thread_id?: string | null;
  file_path?: string;
  prev_content?: string | null;
  new_content?: string | null;
  rollback_from_ts?: string | null;
  scanner?: { decision?: string; reason?: string };
  [key: string]: unknown;
}

async function readDetail(
  response: Response,
  fallback: string,
): Promise<never> {
  const error = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  const message = typeof error.detail === "string" ? error.detail : fallback;
  throw new Error(message);
}

export async function loadCustomSkill(
  skillName: string,
): Promise<CustomSkillContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
  );
  if (!response.ok) {
    await readDetail(response, "Failed to load custom skill");
  }
  return response.json();
}

export async function updateCustomSkill(
  skillName: string,
  content: string,
): Promise<CustomSkillContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content }),
    },
  );
  if (!response.ok) {
    await readDetail(
      response,
      "Failed to save custom skill (the security scanner may have blocked the edit)",
    );
  }
  return response.json();
}

export async function deleteCustomSkill(skillName: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    await readDetail(response, "Failed to delete custom skill");
  }
}

export async function loadCustomSkillHistory(
  skillName: string,
): Promise<CustomSkillHistoryEntry[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/history`,
  );
  if (!response.ok) {
    await readDetail(response, "Failed to load skill history");
  }
  const json = (await response.json()) as {
    history?: CustomSkillHistoryEntry[];
  };
  return Array.isArray(json.history) ? json.history : [];
}

export async function rollbackCustomSkill(
  skillName: string,
  historyIndex: number,
): Promise<CustomSkillContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/rollback`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ history_index: historyIndex }),
    },
  );
  if (!response.ok) {
    await readDetail(
      response,
      "Rollback failed (the security scanner may have blocked the content)",
    );
  }
  return response.json();
}
