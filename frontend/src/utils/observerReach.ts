import type { Message } from '../types';

export const OUTGOING_REACH_DELAY_MS = 20_000;
export const YOUNG_REACH_MS = 60_000;
export const MID_REACH_MS = 10 * 60_000;
export const YOUNG_POLL_MS = 8_000;
export const MID_POLL_MS = 60_000;

export function isObserverReachEligible(msg: Message): boolean {
  if (!msg.packet_hash) return false;
  return msg.observer_reach_eligible !== false;
}

export function isOutgoingReachReady(msg: Message, nowMs: number = Date.now()): boolean {
  if (!msg.outgoing) return true;
  return nowMs - msg.received_at * 1000 >= OUTGOING_REACH_DELAY_MS;
}

export function messageAgeMs(msg: Message, nowMs: number = Date.now()): number {
  return nowMs - msg.received_at * 1000;
}

/** Poll cadence while a flood is still young. Null means keep the last snapshot. */
export function observerReachPollIntervalMs(ageMs: number): number | null {
  if (ageMs < YOUNG_REACH_MS) return YOUNG_POLL_MS;
  if (ageMs < MID_REACH_MS) return MID_POLL_MS;
  return null;
}
