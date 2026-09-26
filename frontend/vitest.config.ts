// Two projects (D9 §5.16): `node` runs each card's pure half, `browser` mounts
// the cards themselves in Chromium in a copy of HA's sections grid.
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      { test: { name: "node", include: ["test/*.test.ts"], environment: "node" } },
      {
        test: {
          name: "browser",
          include: ["test/browser/**/*.test.ts"],
          testTimeout: 60_000,
          browser: {
            enabled: true,
            provider: playwright(),
            headless: true,
            screenshotFailures: false,
            instances: [{ browser: "chromium" }],
          },
        },
      },
    ],
  },
});
