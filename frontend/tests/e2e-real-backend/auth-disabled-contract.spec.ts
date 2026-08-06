import { expect, test } from "@playwright/test";

import { AUTH_DISABLED_USER } from "../../src/core/auth/auth-disabled-user";

// Must follow the config's APP_PORT, not a hardcoded 3000: with something
// else already on 3000 these tests silently drove that app instead and
// failed as if Nova had regressed.
const APP = `http://localhost:${process.env.E2E_PORT ?? "3000"}`;

test.describe("auth-disabled contract (real backend)", () => {
  test("gateway /auth/me returns the frontend synthetic user without a cookie", async ({
    context,
  }) => {
    const resp = await context.request.get(`${APP}/api/v1/auth/me`);

    expect(resp.status(), await resp.text()).toBe(200);
    await expect(resp.json()).resolves.toEqual(AUTH_DISABLED_USER);
  });
});
