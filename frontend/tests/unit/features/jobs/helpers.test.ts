import { describe, expect, it } from "vitest";

import { parsePayload } from "@/features/jobs/components/schedules-panel";
import { pollIntervalFor } from "@/features/jobs/hooks";
import { canCancel, canRetry, toneFor } from "@/features/jobs/status";
import { formatRelative } from "@/features/jobs/time";
import { JOB_STATUSES } from "@/features/jobs/types";

describe("job status presentation", () => {
  it("assigns every contract status a tone", () => {
    for (const s of JOB_STATUSES) expect(toneFor(s)).toBeTruthy();
    expect(toneFor("succeeded")).toBe("success");
    expect(toneFor("dead_letter")).toBe("destructive");
    expect(toneFor("retrying")).toBe("warning");
  });

  it("offers cancel only while live and retry only after failure", () => {
    expect(canCancel("running")).toBe(true);
    expect(canCancel("succeeded")).toBe(false);
    expect(canRetry("dead_letter")).toBe(true);
    expect(canRetry("queued")).toBe(false);
  });
});

describe("pollIntervalFor", () => {
  const job = (status: string) => ({ status }) as never;
  it("polls fast while anything is in flight, slowly when settled", () => {
    expect(pollIntervalFor(undefined)).toBe(false);
    expect(pollIntervalFor([job("running"), job("succeeded")])).toBe(3_000);
    expect(pollIntervalFor([job("succeeded"), job("failed")])).toBe(30_000);
  });
});

describe("parsePayload", () => {
  it("accepts empty and object JSON, rejects arrays and junk", () => {
    expect(parsePayload("")).toEqual({});
    expect(parsePayload('{"a":1}')).toEqual({ a: 1 });
    expect(parsePayload("[1]")).toBeNull();
    expect(parsePayload("nope")).toBeNull();
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-09-19T12:00:00Z");
  it("formats past and future in the largest sensible unit", () => {
    expect(formatRelative("2026-09-19T11:57:00Z", now, "en")).toBe(
      "3 minutes ago",
    );
    expect(formatRelative("2026-09-19T14:00:00Z", now, "en")).toBe(
      "in 2 hours",
    );
    expect(formatRelative("2026-09-17T12:00:00Z", now, "en")).toBe(
      "2 days ago",
    );
  });
  it("is honest about missing or bad input", () => {
    expect(formatRelative(null)).toBe("—");
    expect(formatRelative("not a date")).toBe("—");
  });
});
