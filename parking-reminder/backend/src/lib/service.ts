/**
 * Service-layer wrapper around Prisma used by both the REST API and
 * the Discord bot. Keeps DB access logic in one place and lets the
 * bot/routes stay thin.
 */
import type { ParkingRecord } from "@prisma/client";
import { prisma } from "./prisma";
import { computeLocationNote } from "./parking";

export interface CreateIndoorInput {
  floor: string;
  slot: string;
  userId: string;
}

export interface CreateOutdoorInput {
  latitude: number;
  longitude: number;
  userId: string;
}

export class ParkingService {
  async createIndoor(input: CreateIndoorInput): Promise<ParkingRecord> {
    const locationNote = computeLocationNote({
      isIndoor: true,
      floor: input.floor,
      slot: input.slot,
    });
    return prisma.parkingRecord.create({
      data: {
        floor: input.floor,
        slot: input.slot,
        locationNote,
        isIndoor: true,
        userId: input.userId,
      },
    });
  }

  async createOutdoor(input: CreateOutdoorInput): Promise<ParkingRecord> {
    const locationNote = computeLocationNote({
      isIndoor: false,
      latitude: input.latitude,
      longitude: input.longitude,
    });
    return prisma.parkingRecord.create({
      data: {
        latitude: input.latitude,
        longitude: input.longitude,
        locationNote,
        isIndoor: false,
        userId: input.userId,
      },
    });
  }

  async getLatest(userId: string): Promise<ParkingRecord | null> {
    return prisma.parkingRecord.findFirst({
      where: { userId },
      orderBy: { createdAt: "desc" },
    });
  }
}

export const parkingService = new ParkingService();
