import { ReplitConnectors } from "@replit/connectors-sdk";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

try {
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const connectors = new ReplitConnectors();
  const options = {
    method: request.method || "GET",
    headers: request.headers || {},
  };
  if (request.body_base64) {
    options.body = Buffer.from(request.body_base64, "base64");
  }
  const response = await connectors.proxy("google-drive", request.path, options);
  const body = Buffer.from(await response.arrayBuffer());
  process.stdout.write(JSON.stringify({
    ok: response.ok,
    status: response.status,
    body_base64: body.toString("base64"),
  }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    status: 502,
    error: error instanceof Error ? error.message : String(error),
  }));
  process.exitCode = 1;
}