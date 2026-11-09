import { spawn } from "node:child_process";
import { rmSync } from "node:fs";
import { resolve } from "node:path";

const databasePath = resolve("e2e/undercover-e2e.db");
for (const suffix of ["", "-shm", "-wal"]) {
  rmSync(`${databasePath}${suffix}`, { force: true });
}

const command = process.platform === "win32" ? "uv.cmd" : "uv";
const child = spawn(
  command,
  ["run", "-m", "uvicorn", "backend.main:app", "--port", "8000"],
  {
    env: {
      ...process.env,
      DATABASE_PATH: databasePath,
      DATABASE_URL: "",
      LANGFUSE_SECRET_KEY: "",
      LANGFUSE_PUBLIC_KEY: "",
      LANGFUSE_BASE_URL: "",
      UNDERCOVER_AGENT: "http://127.0.0.1:8003",
    },
    stdio: "inherit",
    shell: process.platform === "win32",
  },
);

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => process.exit(code ?? 0));
