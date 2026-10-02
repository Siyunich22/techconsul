import { defineConfig, devices } from "@playwright/test";

// e2e гоняются против production-сборки frontend (детерминированно, без HMR dev-сервера);
// API, БД, MinIO и Mailpit — из поднятого стека (make up).
// E2E_BASE_URL=http://localhost:3000 — прогнать против уже запущенного web без сборки.
const PORT = 3100;
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: external ?? `http://localhost:${PORT}`,
    locale: "ru-RU",
    trace: "retain-on-failure",
  },
  webServer: external
    ? undefined
    : {
        command: `npm run build && npx next start -p ${PORT}`,
        url: `http://localhost:${PORT}/login`,
        timeout: 300_000,
        reuseExistingServer: !process.env.CI,
        env: { API_INTERNAL_URL: process.env.API_INTERNAL_URL ?? "http://localhost:8000", NEXT_TELEMETRY_DISABLED: "1" },
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
