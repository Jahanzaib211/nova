/**
 * Environment-variable compatibility for the deerflow -> nova rename.
 *
 * Server-side env vars are named `NOVA_<X>`. The old `DEER_FLOW_<X>` name is
 * still honoured as a fallback so existing .env files and compose stacks keep
 * working. Remove the fallback one release after the rename.
 */

export const ENV_PREFIX = "NOVA_";
export const LEGACY_ENV_PREFIX = "DEER_FLOW_";

/** Read `NOVA_<name>`, falling back to `DEER_FLOW_<name>`. */
export function getEnvCompat(
  name: string,
  env: Record<string, string | undefined> = process.env,
): string | undefined {
  return env[`${ENV_PREFIX}${name}`] ?? env[`${LEGACY_ENV_PREFIX}${name}`];
}
