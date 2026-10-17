import type { Message } from '../types';

/**
 * Byte-perfect channel resend window, timed on the BROWSER clock (owner decision
 * 2026-10-17). The server `sender_timestamp` is stamped with the server wall clock,
 * which can be minutes off, so it is never compared with `Date.now()` directly.
 *
 * Anchor of a message, in browser seconds, from most to least trusted:
 * 1. the earliest browser time this tab saw it live (WS `message` event, or the
 *    send anchor from `sendAnchorMs`), when known;
 * 2. `sender_timestamp + offset`, where offset is the browser-minus-server skew
 *    measured on buffered live raw packets (`getMeasuredServerClockOffsetSec()`);
 * 3. with no measured offset, `sender_timestamp` as is. The skew is then unknown,
 *    so the age is not authoritative: the resend request carries no
 *    `client_age_seconds` and the backend checks the window on its own clock. An
 *    anchor more than `UNMEASURED_FUTURE_TOLERANCE_SEC` in the future (server clock
 *    ahead by an unknown amount) gives no age at all, so the button stays hidden
 *    instead of being re-armed at "0 s old" on every render.
 *
 * Only an authoritative age is sent as `client_age_seconds`, which the backend
 * then uses for the 30 s check instead of its own clock.
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

// Without a measured offset, a server stamp up to this far in the future is
// treated as clock jitter (age 0); beyond it the age is unknown.
const UNMEASURED_FUTURE_TOLERANCE_SEC = 2;

export interface ResendAge {
  /** Age in seconds, never negative. */
  ageSec: number;
  /** True when measured on the browser clock (live sighting or measured skew). */
  authoritative: boolean;
}

/**
 * Browser-clock age of a message for the resend window, or null when it cannot be
 * told. `measuredOffsetSec` is null when no skew has been measured.
 */
export function resendAge(
  msg: Pick<Message, 'id' | 'sender_timestamp'>,
  nowMs: number,
  measuredOffsetSec: number | null
): ResendAge | null {
  const nowSec = nowMs / 1000;
  const seenMs = liveSightingsMs.get(msg.id);
  if (seenMs !== undefined) {
    return { ageSec: Math.max(0, nowSec - seenMs / 1000), authoritative: true };
  }
  if (msg.sender_timestamp === null) return null;
  if (measuredOffsetSec !== null) {
    // The offset is in whole seconds, so a fresh message can land up to 1 s in the future.
    const ageSec = nowSec - (msg.sender_timestamp + measuredOffsetSec);
    return { ageSec: Math.max(0, ageSec), authoritative: true };
  }
  const rawAgeSec = nowSec - msg.sender_timestamp;
  if (rawAgeSec < -UNMEASURED_FUTURE_TOLERANCE_SEC) return null;
  return { ageSec: Math.max(0, rawAgeSec), authoritative: false };
}

/**
 * Browser time to record as the live sighting of a message this tab just sent.
 * The server stamps `sender_timestamp` once it holds the radio lock, which can be
 * well after the request left (another radio operation in progress), so the
 * request start is too early. With a measured skew, the stamp converted to browser
 * time is used, bounded by the request start and the response. Without one, the
 * response time is used: it errs late by the in-lock send time only, never early.
 */
export function sendAnchorMs(
  senderTimestamp: number | null,
  requestStartMs: number,
  responseMs: number,
  measuredOffsetSec: number | null
): number {
  if (senderTimestamp === null || measuredOffsetSec === null) return responseMs;
  const stampedMs = (senderTimestamp + measuredOffsetSec) * 1000;
  return Math.min(responseMs, Math.max(requestStartMs, stampedMs));
}
