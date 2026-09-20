import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
  getCsrfHeaders: () => ({ "X-CSRF-Token": "t" }),
}));
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "http://gw" }));

import { fetch } from "@/core/api/fetcher";
import { CapabilityError, invoke, listOps } from "@/core/capabilities/api";
import { CAPABILITY_MODULES, OP_META } from "@/core/capabilities/generated";

const mocked = vi.mocked(fetch);

afterEach(() => mocked.mockReset());

describe("capabilities client", () => {
  it("posts the input to the op endpoint and unwraps result", async () => {
    mocked.mockResolvedValue(
      new Response(
        JSON.stringify({ name: "jobs.list", result: { items: [], total: 0 } }),
        { status: 200 },
      ),
    );
    const out = await invoke("jobs.list", { limit: 5 });
    expect(out).toEqual({ items: [], total: 0 });
    const [calledUrl, init] = mocked.mock.calls[0]!;
    expect(calledUrl).toBe("http://gw/api/capabilities/ops/jobs.list");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ limit: 5 });
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
  });

  it("surfaces the server's detail on failure", async () => {
    mocked.mockResolvedValue(
      new Response(JSON.stringify({ detail: "jobs.enqueue is admin-only" }), {
        status: 403,
      }),
    );
    await expect(invoke("jobs.enqueue", { type: "x" })).rejects.toMatchObject({
      name: "CapabilityError",
      status: 403,
      detail: "jobs.enqueue is admin-only",
    } satisfies Partial<CapabilityError>);
  });

  it("refuses names outside the contract without a network call", async () => {
    // @ts-expect-error — the whole point: not a CapabilityOpName
    await expect(invoke("nope.nothing", {})).rejects.toBeInstanceOf(
      CapabilityError,
    );
    expect(mocked).not.toHaveBeenCalled();
  });

  it("lists ops", async () => {
    mocked.mockResolvedValue(
      new Response(
        JSON.stringify({
          snapshot: { version: 1, modules: [], operations: [] },
          status: {},
          flags: {},
        }),
        { status: 200 },
      ),
    );
    expect((await listOps()).snapshot.version).toBe(1);
  });

  it("generated metadata is consistent: every module op has meta and vice versa", () => {
    const fromModules = new Set(
      CAPABILITY_MODULES.flatMap((m) => [...m.operations]),
    );
    expect(new Set(Object.keys(OP_META))).toEqual(fromModules);
    for (const [name, meta] of Object.entries(OP_META)) {
      expect(name.startsWith(`${meta.module}.`)).toBe(true);
    }
  });
});
