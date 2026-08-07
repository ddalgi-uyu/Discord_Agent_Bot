/**
 * Shared Prisma client singleton.
 *
 * Reuses a single PrismaClient instance across hot-reloads (dev) and
 * normal module imports (prod) to avoid exhausting database connections.
 */
import { PrismaClient } from "@prisma/client";

declare global {
  // eslint-disable-next-line no-var
  var __prisma: PrismaClient | undefined;
}

export const prisma: PrismaClient =
  global.__prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === "production" ? ["error"] : ["error", "warn"],
  });

if (process.env.NODE_ENV !== "production") {
  global.__prisma = prisma;
}
