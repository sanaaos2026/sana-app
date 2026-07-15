import { spawn } from "node:child_process";
import path from "node:path";
import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// In production (REPLIT_DEPLOYMENT=1) Flask is not started by any workflow,
// so we spawn it here as a child process. In development it runs separately
// via the "Sana Flask App" workflow and we leave it alone.
if (process.env["REPLIT_DEPLOYMENT"] === "1") {
  const flaskDir = path.resolve("/home/runner/workspace/sana_full");

  logger.info({ flaskDir }, "Production: spawning Sana Flask app");

  const flask = spawn("python3", ["app.py"], {
    cwd: flaskDir,
    env: {
      ...process.env,
      // Flask must listen on port 5000 so our proxy can reach it.
      PORT: "5000",
    },
    stdio: "pipe",
  });

  flask.stdout.on("data", (data: Buffer) => {
    process.stdout.write(`[flask] ${data}`);
  });

  flask.stderr.on("data", (data: Buffer) => {
    process.stderr.write(`[flask] ${data}`);
  });

  flask.on("exit", (code, signal) => {
    logger.error({ code, signal }, "Flask process exited — shutting down");
    process.exit(code ?? 1);
  });
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
});
