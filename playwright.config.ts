import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "node e2e/start-agent-stub.ts",
      url: "http://127.0.0.1:8003/health",
      reuseExistingServer: true,
    },
    {
      command: "node e2e/start-backend.ts",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: true,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      reuseExistingServer: true,
    },
  ],
});
