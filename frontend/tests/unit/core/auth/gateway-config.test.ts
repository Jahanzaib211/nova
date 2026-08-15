import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const ENV_KEYS = [
  "NODE_ENV",
  "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL",
  "DEER_FLOW_TRUSTED_ORIGINS",
] as const;

type EnvSnapshot = Partial<
  Record<(typeof ENV_KEYS)[number], string | undefined>
>;

function snapshotEnv(): EnvSnapshot {
  const snapshot: EnvSnapshot = {};
  for (const key of ENV_KEYS) {
    snapshot[key] = process.env[key];
  }
  return snapshot;
}

function setEnv(key: (typeof ENV_KEYS)[number], value: string | undefined) {
  // NODE_ENV is typed as a readonly literal union, so we go through the
  // index signature to keep the test compiler-friendly across cases.
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    delete env[key];
  } else {
    env[key] = value;
  }
}

function restoreEnv(snapshot: EnvSnapshot) {
  for (const key of ENV_KEYS) {
    setEnv(key, snapshot[key]);
  }
}

async function loadFreshConfig() {
  vi.resetModules();
  return await import("@/core/auth/gateway-config");
}

describe("getGatewayConfig", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", undefined);
    setEnv("DEER_FLOW_TRUSTED_ORIGINS", undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
  });

  test("throws when DEER_FLOW_INTERNAL_GATEWAY_BASE_URL is missing (development)", async () => {
    setEnv("NODE_ENV", "development");
    const { getGatewayConfig } = await loadFreshConfig();
    expect(() => getGatewayConfig()).toThrow(/DEER_FLOW_INTERNAL_GATEWAY_BASE_URL/);
  });

  test("throws when DEER_FLOW_INTERNAL_GATEWAY_BASE_URL is missing (production)", async () => {
    setEnv("NODE_ENV", "production");
    const { getGatewayConfig } = await loadFreshConfig();
    expect(() => getGatewayConfig()).toThrow(/DEER_FLOW_INTERNAL_GATEWAY_BASE_URL/);
  });

  test("throws when DEER_FLOW_INTERNAL_GATEWAY_BASE_URL is empty string", async () => {
    setEnv("NODE_ENV", "production");
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", "   ");
    const { getGatewayConfig } = await loadFreshConfig();
    expect(() => getGatewayConfig()).toThrow(/DEER_FLOW_INTERNAL_GATEWAY_BASE_URL/);
  });

  test("uses env values verbatim when set, regardless of NODE_ENV", async () => {
    setEnv("NODE_ENV", "production");
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", "https://gw.example.com/");
    setEnv(
      "DEER_FLOW_TRUSTED_ORIGINS",
      "https://app.example.com, https://admin.example.com",
    );

    const { getGatewayConfig } = await loadFreshConfig();
    const cfg = getGatewayConfig();

    expect(cfg.internalGatewayUrl).toBe("https://gw.example.com");
    expect(cfg.trustedOrigins).toEqual([
      "https://app.example.com",
      "https://admin.example.com",
    ]);
  });

  test("trims and filters empty entries in trustedOrigins", async () => {
    setEnv("NODE_ENV", "production");
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", "https://gw.example.com");
    setEnv(
      "DEER_FLOW_TRUSTED_ORIGINS",
      " https://a.example , ,https://b.example ",
    );

    const { getGatewayConfig } = await loadFreshConfig();
    const cfg = getGatewayConfig();

    expect(cfg.trustedOrigins).toEqual([
      "https://a.example",
      "https://b.example",
    ]);
  });

  test("pins the 'gateway' hostname to 192.168.200.3 (docker-compose default)", async () => {
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", "http://gateway:8001");
    setEnv("DEER_FLOW_TRUSTED_ORIGINS", "http://localhost:3000");

    const { getGatewayConfig } = await loadFreshConfig();
    const cfg = getGatewayConfig();

    expect(cfg.internalGatewayUrl).toBe("http://192.168.200.3:8001");
  });

  test("respects DEER_FLOW_DEV_GATEWAY_IP override for the gateway hostname", async () => {
    setEnv("DEER_FLOW_INTERNAL_GATEWAY_BASE_URL", "http://gateway:8001");
    process.env.DEER_FLOW_DEV_GATEWAY_IP = "10.99.99.99";
    setEnv("DEER_FLOW_TRUSTED_ORIGINS", "http://localhost:3000");

    const { getGatewayConfig } = await loadFreshConfig();
    const cfg = getGatewayConfig();

    expect(cfg.internalGatewayUrl).toBe("http://10.99.99.99:8001");
    delete process.env.DEER_FLOW_DEV_GATEWAY_IP;
  });
});
