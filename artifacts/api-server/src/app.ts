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

app.use((req, res) => {
  const proxyReq = http.request(
    {
      host: "127.0.0.1",
      port: SANA_FLASK_PORT,
      path: req.url,
      method: req.method,
      headers: req.headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
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

  req.pipe(proxyReq, { end: true });
});

export default app;
