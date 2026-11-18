import { defineConfig } from "@playwright/test";

const port = Number(process.env.PULSE_E2E_PORT ?? 8089);

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...(process.env.PULSE_E2E_CHROME ? { launchOptions: { executablePath: process.env.PULSE_E2E_CHROME } } : {}),
  },
  webServer: {
    // Demo mode: realistic fake data, no network access needed.
    command: `cd ../backend && rm -rf ../.e2e-data && PULSE_DEMO=1 PULSE_DATA_DIR=../.e2e-data PULSE_LOG_JSON=false PULSE_ADMIN_USERNAME=admin PULSE_ADMIN_PASSWORD=e2e-password-123 uv run pulse serve --port ${port}`,
    url: `http://127.0.0.1:${port}/readyz`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
