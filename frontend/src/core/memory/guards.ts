/**
 * Runtime validation for `UserMemory` payloads.
 *
 * Every memory endpoint's response goes through `isUserMemory` before it
 * reaches a component. Without it a partially-shaped payload (a proxy error
 * page, an older gateway, a feature-disabled stub) was cast to `UserMemory`
 * and dereferenced in render — `memory.user.workContext.summary` threw and the
 * route-level boundary replaced the entire settings page.
 */
import type { UserMemory } from "./types";

export class MalformedMemoryError extends Error {
  constructor(message = "Memory payload has an unexpected shape") {
    super(message);
    this.name = "MalformedMemoryError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isMemorySection(value: unknown): value is {
  summary: string;
  updatedAt: string;
} {
  return (
    isRecord(value) &&
    typeof value.summary === "string" &&
    typeof value.updatedAt === "string"
  );
}

export function isMemoryFact(
  value: unknown,
): value is UserMemory["facts"][number] {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.content === "string" &&
    typeof value.category === "string" &&
    typeof value.confidence === "number" &&
    Number.isFinite(value.confidence) &&
    typeof value.createdAt === "string" &&
    typeof value.source === "string"
  );
}

export function isUserMemory(value: unknown): value is UserMemory {
  if (!isRecord(value)) return false;
  if (
    typeof value.version !== "string" ||
    typeof value.lastUpdated !== "string" ||
    !isRecord(value.user) ||
    !isRecord(value.history) ||
    !Array.isArray(value.facts)
  ) {
    return false;
  }
  return (
    isMemorySection(value.user.workContext) &&
    isMemorySection(value.user.personalContext) &&
    isMemorySection(value.user.topOfMind) &&
    isMemorySection(value.history.recentMonths) &&
    isMemorySection(value.history.earlierContext) &&
    isMemorySection(value.history.longTermBackground) &&
    value.facts.every(isMemoryFact)
  );
}
