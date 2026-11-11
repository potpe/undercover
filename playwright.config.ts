import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "node e2e/start-judge-stub.ts",
      url: "http://127.0.0.1:8006/health",
      reuseExistingServer: true,
    },
    {
      command: "node e2e/start-auth-stub.ts",
      url: "http://127.0.0.1:8005/health",
      reuseExistingServer: true,
    },
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
      command: "npx vite dashboard --port 5174 --strictPort --mode test",
      url: "http://localhost:5174",
      reuseExistingServer: true,
      env: {
        VITE_SUPABASE_URL: "http://127.0.0.1:8005",
        VITE_SUPABASE_PUBLISHABLE_KEY: "e2e-publishable-key",
      },
    },
  ],
});
