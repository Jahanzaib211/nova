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
