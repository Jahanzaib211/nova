import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Model selector", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);

    // Override the shared empty-list default with a mix of visible and
    // hidden models, mirroring the real config.yaml shape (hidden models
    // stay fully configured — this only trims the quick picker's default
    // surface, see backend ModelConfig.hidden / frontend Model.hidden).
    await page.route("**/api/models", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            models: [
              {
                name: "minimax-m3",
                model: "MiniMax-M3",
                display_name: "MiniMax M3",
                hidden: false,
                max_input_tokens: 1000000,
                supports_vision: true,
                supports_thinking: true,
              },
              {
                name: "fireworks-minimax-m3",
                model: "accounts/fireworks/models/minimax-m3",
                display_name: "MiniMax M3 (AMD MI300X via Fireworks)",
                hidden: true,
                max_input_tokens: 512000,
                amd_compute: "AMD Instinct",
              },
            ],
            token_usage: { enabled: false },
          }),
        });
      }
      return route.fallback();
    });
  });

  test("hides models flagged hidden from the default picker", async ({
    page,
  }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await page
      .getByRole("button", { name: /MiniMax M3/i })
      .first()
      .click();

    await expect(
      page.getByRole("option", { name: "MiniMax M3" }),
    ).toBeVisible();
    await expect(page.getByRole("option", { name: /Fireworks/i })).toBeHidden();
    await expect(
      page.getByTestId("model-picker-show-hidden"),
    ).not.toBeChecked();
  });

  test("show hidden models reveals them and they can be selected", async ({
    page,
  }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await page
      .getByRole("button", { name: /MiniMax M3/i })
      .first()
      .click();

    const hiddenOption = page.getByRole("option", { name: /Fireworks/i });
    await expect(hiddenOption).toBeHidden();

    await page.getByTestId("model-picker-show-hidden").click();
    await expect(hiddenOption).toBeVisible();
    await expect(hiddenOption.getByText("Hidden")).toBeVisible();

    await hiddenOption.click();

    // The picker closes and the trigger reports the newly chosen model.
    await expect(
      page.getByRole("button", { name: /MiniMax M3 \(AMD MI300X/i }).first(),
    ).toBeVisible();
  });
});
