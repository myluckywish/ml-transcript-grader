import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { spawn } from "node:child_process";

const rootDir = process.cwd();
const backendDir = resolve(rootDir, "backend");
const venvPython = resolve(backendDir, ".venv/bin/python");
const pythonBin = existsSync(venvPython) ? venvPython : (process.env.PYTHON_BIN || "python3");
const parserPort = process.env.PARSER_PORT || "8000";

function run(name, command, args, cwd = rootDir) {
  const child = spawn(command, args, {
    cwd,
    env: process.env,
    stdio: "inherit",
  });

  child.on("exit", (code, signal) => {
    if (signal) {
      console.log(`[${name}] exited with signal ${signal}`);
      return;
    }
    if (code !== 0) {
      console.log(`[${name}] exited with code ${code}`);
      if (name === "backend") {
        console.log(
          "[backend] Setup required. Run:\n" +
            "  cd backend\n" +
            "  python -m venv .venv\n" +
            "  source .venv/bin/activate\n" +
            "  pip install -r requirements.txt\n" +
            "Then run: npm run dev:stack",
        );
      }
    }
  });

  return child;
}

const backend = run(
  "backend",
  pythonBin,
  ["-m", "uvicorn", "main:app", "--reload", "--port", parserPort, "--log-level", "debug"],
  backendDir,
);
const frontend = run("frontend", "npm", ["run", "dev:frontend"], rootDir);

function shutdown(signal) {
  if (!backend.killed) {
    backend.kill(signal);
  }
  if (!frontend.killed) {
    frontend.kill(signal);
  }
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

backend.on("exit", () => {
  if (!frontend.killed) {
    frontend.kill("SIGTERM");
  }
});

frontend.on("exit", () => {
  if (!backend.killed) {
    backend.kill("SIGTERM");
  }
});

if (!existsSync(venvPython)) {
  console.warn(
    "[backend] No virtualenv python found at backend/.venv/bin/python. " +
      "Using python3 from PATH; ensure dependencies are installed.",
  );
}
