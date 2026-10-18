import type { Message } from '../../types';

/** Distance from the bottom, in px, that still counts as "pinned to the bottom". */
export const FOLLOW_THRESHOLD_PX = 48;

/**
 * Rows that arrived while the reader was away from the bottom: newer than the
 * newest row held when follow turned off, and never a `historical` one (an old
 * message that only got a new id from a decryption pass).
 */
export function countArrivedSince(rows: readonly Message[], baselineId: number): number {
  let count = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    // Rows are sorted by (received_at, id), not by id, so no early exit on id.
    if (row.id > baselineId && row.historical !== true) count++;
  }
  return count;
}

export function highestId(rows: readonly Message[]): number {
  let max = 0;
  for (const row of rows) if (row.id > max) max = row.id;
  return max;
}
