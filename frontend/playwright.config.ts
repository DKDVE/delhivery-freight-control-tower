import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const venv = process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python";
const py = existsSync(venv) ? venv.slice(3) : "python"; // the server runs with cwd ".."

// Runs against the production build served by FastAPI (DEMO mode, scratch audit log).
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:8000", channel: "msedge", trace: "retain-on-failure" },
  webServer: {
    command: `"${py}" -m uvicorn backend.main:app --port 8000`,
    cwd: "..",
    url: "http://127.0.0.1:8000/api/health",
    reuseExistingServer: true,
    timeout: 120_000,
    env: { DEMO_MODE: "1", AUDIT_LOG_DIR: "app/logs/e2e", PYTHONIOENCODING: "utf-8" },
  },
});
