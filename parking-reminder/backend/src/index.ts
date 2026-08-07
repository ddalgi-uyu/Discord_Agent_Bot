/**
 * Entry point for the backend.
 *
 * Wires up:
 *   - Express REST API (routes mounted under /api)
 *   - CORS middleware
 *   - JSON body parsing
 *   - Discord bot startup (if DISCORD_TOKEN is present)
 *   - Graceful shutdown for SIGINT / SIGTERM
 */
import * as dotenv from "dotenv";
import * as path from "node:path";
import * as fs from "node:fs";

// Load .env from the backend root regardless of CWD.
const envPath = path.resolve(__dirname, "..", ".env");
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
} else {
  dotenv.config();
}

import express, { type Request, type Response, type NextFunction } from "express";
import cors from "cors";
import parkingRouter from "./routes/parking";
import { prisma } from "./lib/prisma";

const PORT = Number(process.env.PORT ?? 3000);

async function bootstrap(): Promise<void> {
  // Fail fast if the DB isn't reachable — avoids serving a broken API.
  try {
    await prisma.$connect();
    console.log("[db] Prisma connected");
  } catch (err) {
    console.error("[db] Failed to connect to database:", err);
    process.exit(1);
  }

  const app = express();
  app.use(cors());
  app.use(express.json({ limit: "64kb" }));

  app.use("/api", parkingRouter);

  // Root health endpoint (handy for uptime monitors that hit `/`).
  app.get("/", (_req: Request, res: Response) => {
    res.json({
      service: "parking-reminder-backend",
      endpoints: [
        "POST /api/park",
        "GET  /api/park/latest?user_id=...",
        "GET  /api/park/history?user_id=...&limit=...",
        "DELETE /api/park/:id",
        "GET  /api/health",
      ],
    });
  });

  // 404 handler for unknown REST routes.
  app.use("/api", (_req: Request, res: Response) => {
    res.status(404).json({ error: "Not found" });
  });

  // Centralised error handler.
  app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
    console.error("[api] Unhandled error:", err);
    const message = err instanceof Error ? err.message : "Internal server error";
    res.status(500).json({ error: message });
  });

  app.listen(PORT, () => {
    console.log(`[api] Listening on http://localhost:${PORT}`);
  });

  // Start the Discord bot if credentials are present. The bot module is
  // dynamic-imported so an invalid token doesn't crash the REST server.
  if (process.env.DISCORD_TOKEN && process.env.DISCORD_CLIENT_ID) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { startBot } = require("./bot") as typeof import("./bot");
      startBot().catch((err: unknown) => {
        console.error("[bot] Failed to start:", err);
      });
    } catch (err) {
      console.error("[bot] Failed to load bot module:", err);
    }
  } else {
    console.warn(
      "[bot] DISCORD_TOKEN / DISCORD_CLIENT_ID not set — bot will not start."
    );
  }
}

async function shutdown(signal: string): Promise<void> {
  console.log(`\n[shutdown] Received ${signal}, draining connections...`);
  try {
    await prisma.$disconnect();
  } catch (err) {
    console.error("[shutdown] Error disconnecting Prisma:", err);
  }
  process.exit(0);
}

process.on("SIGINT", () => {
  void shutdown("SIGINT");
});
process.on("SIGTERM", () => {
  void shutdown("SIGTERM");
});

bootstrap().catch((err) => {
  console.error("[fatal] Bootstrap failed:", err);
  process.exit(1);
});
