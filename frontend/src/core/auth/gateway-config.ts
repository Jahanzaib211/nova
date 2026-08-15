import { z } from "zod";

const gatewayConfigSchema = z.object({
  internalGatewayUrl: z.string().url(),
  trustedOrigins: z.array(z.string()).min(1),
});

export type GatewayConfig = z.infer<typeof gatewayConfigSchema>;

let _cached: GatewayConfig | null = null;

/**
 * Container DNS sometimes returns SERVFAIL for service names (gateway,
 * frontend, …) after a docker network restart. As a defense-in-depth
 * fallback we keep a list of hostnames that should resolve to the
 * gateway's known bridge IP — if the env var is set to a hostname that
 * is in this list, we pin to the IP directly so the SSR fetch never
 * hits Docker's flaky resolver.
 */
const _KNOWN_GATEWAY_HOSTNAMES: Record<string, string> = {
  gateway: process.env.DEER_FLOW_DEV_GATEWAY_IP ?? "192.168.200.3",
};

function _pinHostToIp(url: string): string {
  try {
    const parsed = new URL(url);
    const hostname = parsed.hostname;
    const pinned = _KNOWN_GATEWAY_HOSTNAMES[hostname];
    if (pinned) {
      parsed.hostname = pinned;
      return parsed.toString().replace(/\/+$/, "");
    }
  } catch {
    // unparseable URL → return as-is and let zod catch it later
  }
  return url.replace(/\/+$/, "");
}

export function getGatewayConfig(): GatewayConfig {
  if (_cached) return _cached;

  // 2026-08-15: refuse to start with the empty fallback. The previous
  // silent fallback to ``http://127.0.0.1:8001`` produced four ECONNREFUSED
  // bursts in pm2 logs (the gateway is never reachable from inside the
  // frontend container at 127.0.0.1) and made the chat UI show
  // "failed to fetch" toasts. A loud failure at startup is far cheaper than
  // a silent, hard-to-diagnose half-hour outage.
  const rawUrl = process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL?.trim();
  if (!rawUrl || rawUrl.length === 0) {
    throw new Error(
      "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL is required but was empty or unset. " +
        "Set it in docker-compose (or the frontend .env) before starting the Next.js server.",
    );
  }
  const internalGatewayUrl = _pinHostToIp(rawUrl);

  const rawOrigins = process.env.DEER_FLOW_TRUSTED_ORIGINS?.trim();
  const trustedOrigins = rawOrigins
    ? rawOrigins
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : ["http://localhost:3000"];

  _cached = gatewayConfigSchema.parse({ internalGatewayUrl, trustedOrigins });
  return _cached;
}
