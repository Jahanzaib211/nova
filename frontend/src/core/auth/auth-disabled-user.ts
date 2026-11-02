import { getEnvCompat } from "../compat/env";

import type { User } from "./types";

export const AUTH_DISABLED_USER: User = {
  id: "default",
  email: "default@test.local",
  system_role: "admin",
  needs_setup: false,
  plan: "free",
  plan_status: null,
  tos_accepted_version: null,
  referral_code: null,
};

const PRODUCTION_ENV_VALUES = new Set(["prod", "production"]);

function isExplicitProductionEnvironment() {
  return [getEnvCompat("ENV"), process.env.ENVIRONMENT].some((value) =>
    PRODUCTION_ENV_VALUES.has((value ?? "").trim().toLowerCase()),
  );
}

export function isAuthDisabledMode() {
  return (
    getEnvCompat("AUTH_DISABLED") === "1" && !isExplicitProductionEnvironment()
  );
}
