import type { Message } from '../types';

export const OUTGOING_REACH_DELAY_MS = 0;
export const YOUNG_REACH_MS = 60_000;
export const MID_REACH_MS = 10 * 60_000;
export const YOUNG_POLL_MS = 2_000;
export const MID_POLL_MS = 60_000;

export function isObserverReachEligible(msg: Message): boolean {
  if (!msg.packet_hash) return false;
  return msg.observer_reach_eligible !== false;
}

/**
 * `received_at` is stamped by the server clock while `nowMs` comes from the browser.
 * `serverClockOffsetSec` is how far the browser is ahead of the server (see
 * `estimateServerClockOffsetSec`), so the age is measured on one clock.
 */
export function messageAgeMs(
  msg: Message,
  nowMs: number = Date.now(),
  serverClockOffsetSec: number = 0
): number {
  return nowMs - (msg.received_at + serverClockOffsetSec) * 1000;
}

export function isOutgoingReachReady(
  msg: Message,
  nowMs: number = Date.now(),
  serverClockOffsetSec: number = 0
): boolean {
  if (!msg.outgoing) return true;
  return messageAgeMs(msg, nowMs, serverClockOffsetSec) >= OUTGOING_REACH_DELAY_MS;
}

/** Poll cadence while a flood is still young. Null means keep the last snapshot. */
export function observerReachPollIntervalMs(ageMs: number): number | null {
  if (ageMs < YOUNG_REACH_MS) return YOUNG_POLL_MS;
  if (ageMs < MID_REACH_MS) return MID_POLL_MS;
  return null;
}
