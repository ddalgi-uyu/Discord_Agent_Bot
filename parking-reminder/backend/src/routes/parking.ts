/**
 * Parking REST API routes.
 *
 * Endpoints:
 *   POST   /api/park          - Create a parking record
 *   GET    /api/park/latest   - Get the most recent record (per user)
 *   GET    /api/park/history  - Get last N records (per user, newest first)
 *   DELETE /api/park/:id      - Delete a specific record
 *   GET    /api/health        - Lightweight liveness probe
 */
import { Router, type Request, type Response } from "express";
import { prisma } from "../lib/prisma";
import { computeLocationNote } from "../lib/parking";

const router = Router();

const DEFAULT_USER_ID = "default";
const DEFAULT_HISTORY_LIMIT = 10;
const MAX_HISTORY_LIMIT = 50;

/* -------------------------------------------------------------------------- */
/*  Validation helpers                                                         */
/* -------------------------------------------------------------------------- */

interface ParkRequestBody {
  floor?: unknown;
  slot?: unknown;
  latitude?: unknown;
  longitude?: unknown;
  is_indoor?: unknown;
  user_id?: unknown;
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

/* -------------------------------------------------------------------------- */
/*  Serialization                                                              */
/* -------------------------------------------------------------------------- */

import type { ParkingRecord as ParkingRecordModel } from "@prisma/client";

function serializeRecord(record: ParkingRecordModel): Record<string, unknown> {
  return {
    id: record.id,
    floor: record.floor,
    slot: record.slot,
    latitude: record.latitude,
    longitude: record.longitude,
    is_indoor: record.isIndoor,
    location_note: record.locationNote,
    user_id: record.userId,
    timestamp: record.createdAt.toISOString(),
  };
}

/* -------------------------------------------------------------------------- */
/*  GET /api/health                                                            */
/* -------------------------------------------------------------------------- */

router.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "parking-reminder-backend" });
});

/* -------------------------------------------------------------------------- */
/*  POST /api/park                                                             */
/* -------------------------------------------------------------------------- */

router.post("/park", async (req: Request, res: Response) => {
  const body = (req.body ?? {}) as ParkRequestBody;
  const isIndoorRaw = asBoolean(body.is_indoor);
  if (isIndoorRaw === null) {
    return res.status(400).json({
      error: "is_indoor must be a boolean (true/false)",
    });
  }

  const userId = asString(body.user_id) ?? DEFAULT_USER_ID;
  const isIndoor = isIndoorRaw;

  let locationNote: string;
  let data: {
    latitude?: number;
    longitude?: number;
    floor?: string;
    slot?: string;
  };

  if (isIndoor) {
    const floor = asString(body.floor);
    const slot = asString(body.slot);
    if (!floor || !slot) {
      return res.status(400).json({
        error: "Indoor records require both `floor` and `slot` as non-empty strings",
      });
    }
    data = { floor, slot };
    locationNote = computeLocationNote({ isIndoor: true, floor, slot });
  } else {
    const latitude = asNumber(body.latitude);
    const longitude = asNumber(body.longitude);
    if (latitude === null || longitude === null) {
      return res.status(400).json({
        error: "Outdoor records require numeric `latitude` and `longitude`",
      });
    }
    if (latitude < -90 || latitude > 90) {
      return res.status(400).json({ error: "latitude must be between -90 and 90" });
    }
    if (longitude < -180 || longitude > 180) {
      return res.status(400).json({ error: "longitude must be between -180 and 180" });
    }
    data = { latitude, longitude };
    locationNote = computeLocationNote({
      isIndoor: false,
      latitude,
      longitude,
    });
  }

  try {
    const record = await prisma.parkingRecord.create({
      data: {
        ...data,
        locationNote,
        isIndoor,
        userId,
      },
    });
    return res.status(201).json({
      id: record.id,
      status: "saved",
      timestamp: record.createdAt.toISOString(),
      location_note: record.locationNote,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown database error";
    return res.status(500).json({ error: `Failed to save record: ${message}` });
  }
});

/* -------------------------------------------------------------------------- */
/*  GET /api/park/latest                                                       */
/* -------------------------------------------------------------------------- */

router.get("/park/latest", async (req: Request, res: Response) => {
  const userId =
    typeof req.query.user_id === "string" && req.query.user_id.trim() !== ""
      ? req.query.user_id.trim()
      : DEFAULT_USER_ID;

  try {
    const record = await prisma.parkingRecord.findFirst({
      where: { userId },
      orderBy: { createdAt: "desc" },
    });
    if (!record) {
      return res.status(404).json({
        error: "No parking record found",
        user_id: userId,
      });
    }
    return res.json(serializeRecord(record));
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown database error";
    return res.status(500).json({ error: `Failed to fetch record: ${message}` });
  }
});

/* -------------------------------------------------------------------------- */
/*  GET /api/park/history                                                      */
/* -------------------------------------------------------------------------- */

router.get("/park/history", async (req: Request, res: Response) => {
  const userId =
    typeof req.query.user_id === "string" && req.query.user_id.trim() !== ""
      ? req.query.user_id.trim()
      : DEFAULT_USER_ID;

  let limit = DEFAULT_HISTORY_LIMIT;
  if (typeof req.query.limit === "string" && req.query.limit.trim() !== "") {
    const parsed = Number(req.query.limit);
    if (!Number.isFinite(parsed) || parsed < 1) {
      return res.status(400).json({ error: "limit must be a positive integer" });
    }
    limit = Math.min(Math.floor(parsed), MAX_HISTORY_LIMIT);
  }

  try {
    const records = await prisma.parkingRecord.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
      take: limit,
    });
    return res.json(records.map(serializeRecord));
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown database error";
    return res.status(500).json({ error: `Failed to fetch history: ${message}` });
  }
});

/* -------------------------------------------------------------------------- */
/*  DELETE /api/park/:id                                                       */
/* -------------------------------------------------------------------------- */

router.delete("/park/:id", async (req: Request, res: Response) => {
  const id = typeof req.params.id === "string" ? req.params.id.trim() : "";
  if (!id) {
    return res.status(400).json({ error: "Missing record id" });
  }

  try {
    const existing = await prisma.parkingRecord.findUnique({ where: { id } });
    if (!existing) {
      return res.status(404).json({ error: "Record not found", id });
    }
    await prisma.parkingRecord.delete({ where: { id } });
    return res.json({
      id,
      status: "deleted",
      timestamp: new Date().toISOString(),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown database error";
    return res.status(500).json({ error: `Failed to delete record: ${message}` });
  }
});

export default router;
