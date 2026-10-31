import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();

vi.mock("@/core/api/fetcher", () => ({
  fetch: (...args: unknown[]) => fetchMock(...args),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://gateway.test",
}));

describe("loadCapabilities", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ circuits: [] }),
    });
  });

  it("calls the unscoped endpoint when no thread is given", async () => {
    const { loadCapabilities } = await import("@/core/runtime/api");
    await loadCapabilities();
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/runtime/capabilities",
    );
  });

  it("scopes the circuit snapshot to the chat being rendered", async () => {
    // The runtime bar sits above ONE chat's Agent's Computer. Without the
    // scope, a breaker opened by another thread painted this chat "Degraded".
    const { loadCapabilities } = await import("@/core/runtime/api");
    await loadCapabilities("thread-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/runtime/capabilities?thread_id=thread-1",
    );
  });

  it("encodes ids that need escaping", async () => {
    const { loadCapabilities } = await import("@/core/runtime/api");
    await loadCapabilities("a/b c");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/runtime/capabilities?thread_id=a%2Fb%20c",
    );
  });
});
