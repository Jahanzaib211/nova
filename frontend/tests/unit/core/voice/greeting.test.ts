import { describe, expect, it } from "vitest";

import { displayNameFrom, greetingFor, partOfDay } from "@/core/voice/greeting";

describe("partOfDay", () => {
  it.each([
    ["2026-08-06T06:00:00", "morning"],
    ["2026-08-06T11:59:00", "morning"],
    ["2026-08-06T12:00:00", "afternoon"],
    ["2026-08-06T17:59:00", "afternoon"],
    ["2026-08-06T18:00:00", "evening"],
    ["2026-08-06T23:30:00", "evening"],
  ])("%s is %s", (iso, expected) => {
    expect(partOfDay(new Date(iso))).toBe(expected);
  });
});

describe("displayNameFrom", () => {
  it("strips digits from the local part", () => {
    expect(displayNameFrom("alijatt2323@gmail.com")).toBe("Alijatt");
  });

  it("takes the first segment of a separated address", () => {
    expect(displayNameFrom("jane.doe@example.com")).toBe("Jane");
    expect(displayNameFrom("jane_doe@example.com")).toBe("Jane");
    expect(displayNameFrom("jane+tag@example.com")).toBe("Jane");
  });

  it("normalizes capitalization", () => {
    expect(displayNameFrom("JANE@example.com")).toBe("Jane");
  });

  it("declines role addresses rather than greeting an alias", () => {
    for (const alias of [
      "admin@x.com",
      "no-reply@x.com",
      "support@x.com",
      "info@x.com",
    ]) {
      expect(displayNameFrom(alias)).toBeNull();
    }
  });

  it("declines when nothing name-like survives", () => {
    expect(displayNameFrom("x1@example.com")).toBeNull();
    expect(displayNameFrom("12345@example.com")).toBeNull();
    expect(displayNameFrom(null)).toBeNull();
    expect(displayNameFrom(undefined)).toBeNull();
  });
});

describe("greetingFor", () => {
  const evening = new Date("2026-08-06T19:00:00");

  it("welcomes a new account differently from a returning one", () => {
    expect(greetingFor("signup", "Jane")).toContain("Welcome to Nova");
    expect(greetingFor("login", "Jane", evening)).toContain("Good evening");
  });

  it("uses only the first name", () => {
    expect(greetingFor("login", "Jane Doe", evening)).toContain("Jane");
    expect(greetingFor("login", "Jane Doe", evening)).not.toContain("Doe");
  });

  it("reads naturally with no name at all", () => {
    const words = greetingFor("login", null, evening);
    expect(words).toContain("Good evening");
    // No dangling comma where the name would have gone.
    expect(words).not.toMatch(/,\s*[.?]/);
    expect(words).not.toContain(" ,");
  });

  it("stays short enough to be spoken without becoming an interruption", () => {
    for (const kind of ["signup", "login", "return"] as const) {
      expect(greetingFor(kind, "Jane").length).toBeLessThan(90);
    }
  });
});
