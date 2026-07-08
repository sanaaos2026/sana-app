import http from "node:http";
import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());

app.use(router);

// This service's own routes (health checks etc.) are handled above. Every
// other request is forwarded as-is to the Sana Flask app, which is the
// actual product served at the workspace root. This keeps the Flask app's
// code untouched while letting it live behind the workspace's path-based
// proxy (which only knows how to route to registered artifact services).
const SANA_FLASK_PORT = 5000;

// Hop-by-hop headers must not be forwarded by a proxy (RFC 7230 §6.1).
const HOP_BY_HOP_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

function stripHopByHopHeaders(
  headers: http.IncomingHttpHeaders,
): http.IncomingHttpHeaders {
  const cleaned = { ...headers };
  for (const header of HOP_BY_HOP_HEADERS) {
    delete cleaned[header];
  }
  return cleaned;
}

app.use((req, res) => {
  const proxyReq = http.request(
    {
      host: "127.0.0.1",
      port: SANA_FLASK_PORT,
      path: req.url,
      method: req.method,
      headers: stripHopByHopHeaders(req.headers),
    },
    (proxyRes) => {
      res.writeHead(
        proxyRes.statusCode ?? 502,
        stripHopByHopHeaders(proxyRes.headers),
      );
      proxyRes.pipe(res, { end: true });
    },
  );

  proxyReq.on("error", (err) => {
    logger.error({ err }, "Error proxying request to Sana Flask app");
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "text/plain" });
    }
    res.end("Bad Gateway: could not reach the Sana Flask app.");
  });

  // If the client disconnects mid-request, stop waiting on the upstream
  // Flask app instead of leaving the connection dangling.
  res.on("close", () => {
    if (!res.writableEnded) {
      proxyReq.destroy();
    }
  });

  req.pipe(proxyReq, { end: true });
});

export default app;
