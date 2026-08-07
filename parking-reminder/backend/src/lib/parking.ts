/**
 * Parking domain helpers.
 *
 * - `computeLocationNote` derives the human-readable `locationNote`
 *   stored on every record.
 * - `formatRelativeTime` is used by the Discord bot and (optionally)
 *   by the REST layer to format timestamps as "2 hours ago"-style
 *   strings without dragging in a heavyweight date library.
 */
export interface LocationInput {
  latitude?: number | null;
  longitude?: number | null;
  floor?: string | null;
  slot?: string | null;
  isIndoor: boolean;
}

/** Build the location note persisted on the record. */
export function computeLocationNote(input: LocationInput): string {
  if (input.isIndoor) {
    const floor = (input.floor ?? "").trim();
    const slot = (input.slot ?? "").trim();
    if (!floor && !slot) {
      return "Indoor (unspecified)";
    }
    return `${floor}-${slot}`;
  }
  if (input.latitude != null && input.longitude != null) {
    return `https://www.google.com/maps?q=${input.latitude},${input.longitude}`;
  }
  return "Outdoor (unspecified)";
}

/**
 * Format a Date as a coarse human-friendly "N units ago" string.
 * Used by Discord replies — kept dependency-free.
 */
export function formatRelativeTime(date: Date, now: Date = new Date()): string {
  const diffMs = now.getTime() - date.getTime();
  if (diffMs < 0) {
    return "in the future";
  }
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 30) {
    return "just now";
  }
  if (seconds < 60) {
    return `${seconds} seconds ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return days === 1 ? "1 day ago" : `${days} days ago`;
  }
  const months = Math.floor(days / 30);
  if (months < 12) {
    return months === 1 ? "1 month ago" : `${months} months ago`;
  }
  const years = Math.floor(days / 365);
  return years === 1 ? "1 year ago" : `${years} years ago`;
}
