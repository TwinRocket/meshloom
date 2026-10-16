import type { Message } from '../types';

/**
 * Byte-perfect channel resend window, timed on the BROWSER clock (owner decision
 * 2026-10-17). The server `sender_timestamp` is stamped with the server wall clock,
 * which can be minutes off, so it is never compared with `Date.now()` directly.
 *
 * Anchor of a message, in browser seconds:
 * 1. the earliest browser time this tab saw it live (send request start, send
 *    response, or WS `message` event), when known;
 * 2. otherwise `sender_timestamp + offset`, where offset is the browser-minus-server
 *    skew measured on buffered live raw packets (`getServerClockOffsetSec()`, 0 when
 *    nothing is buffered).
 *
 * The resend request carries the resulting age (`client_age_seconds`), so the
 * backend checks the 30 s window against the browser's view, not its own clock.
 */
export const RESEND_WINDOW_SECONDS = 30;

// Live sightings older than this can no longer matter for a 30 s window.
const LIVE_SIGHTING_RETENTION_MS = 10 * 60 * 1000;

const liveSightingsMs = new Map<number, number>();

function pruneLiveSightings(nowMs: number): void {
  for (const [id, seenMs] of liveSightingsMs) {
    if (nowMs - seenMs > LIVE_SIGHTING_RETENTION_MS) liveSightingsMs.delete(id);
  }
}

/** Record that this tab saw an outgoing channel message live, at browser time `atMs`. */
export function noteLiveOutgoingChannelMessage(
  msg: Pick<Message, 'id' | 'type' | 'outgoing'>,
  atMs: number = Date.now()
): void {
  if (!msg.outgoing || msg.type !== 'CHAN') return;
  const previous = liveSightingsMs.get(msg.id);
  if (previous === undefined || atMs < previous) liveSightingsMs.set(msg.id, atMs);
  pruneLiveSightings(atMs);
}

/** Test helper. */
export function resetLiveOutgoingSightings(): void {
  liveSightingsMs.clear();
}

/** Browser-clock age of a message for the resend window, in seconds (never negative). */
export function resendAgeSec(
  msg: Pick<Message, 'id' | 'sender_timestamp'>,
  nowMs: number,
  serverClockOffsetSec: number
): number | null {
  const seenMs = liveSightingsMs.get(msg.id);
  let anchorSec: number;
  if (seenMs !== undefined) {
    anchorSec = seenMs / 1000;
  } else if (msg.sender_timestamp !== null) {
    anchorSec = msg.sender_timestamp + serverClockOffsetSec;
  } else {
    return null;
  }
  return Math.max(0, nowMs / 1000 - anchorSec);
}
