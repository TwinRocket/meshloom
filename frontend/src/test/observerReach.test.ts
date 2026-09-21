import { describe, expect, it } from 'vitest';

import type { Message } from '../types';
import {
  isObserverReachEligible,
  MID_POLL_MS,
  MID_REACH_MS,
  observerReachPollIntervalMs,
  YOUNG_POLL_MS,
  YOUNG_REACH_MS,
} from '../utils/observerReach';

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Alice: hello',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: 'Alice',
    packet_hash: 'AABBCCDDEEFF0011',
    observer_reach_eligible: true,
    ...overrides,
  };
}

describe('isObserverReachEligible', () => {
  it('allows channel messages with a hash', () => {
    expect(isObserverReachEligible(message({ type: 'CHAN' }))).toBe(true);
  });

  it('rejects PRIV when eligible is explicitly false', () => {
    expect(
      isObserverReachEligible(
        message({
          type: 'PRIV',
          observer_reach_eligible: false,
        })
      )
    ).toBe(false);
  });

  it('allows PRIV with a hash when eligible is null or true', () => {
    expect(
      isObserverReachEligible(
        message({
          type: 'PRIV',
          observer_reach_eligible: null,
        })
      )
    ).toBe(true);
    expect(
      isObserverReachEligible(
        message({
          type: 'PRIV',
          observer_reach_eligible: true,
        })
      )
    ).toBe(true);
  });

  it('allows flood DMs with a hash', () => {
    expect(
      isObserverReachEligible(
        message({
          type: 'PRIV',
          observer_reach_eligible: true,
        })
      )
    ).toBe(true);
  });

  it('rejects messages without a hash', () => {
    expect(isObserverReachEligible(message({ packet_hash: null }))).toBe(false);
  });
});

describe('observerReachPollIntervalMs', () => {
  it('polls every 2s under one minute', () => {
    expect(observerReachPollIntervalMs(30_000)).toBe(YOUNG_POLL_MS);
    expect(observerReachPollIntervalMs(YOUNG_REACH_MS - 1)).toBe(YOUNG_POLL_MS);
  });

  it('polls every 60s between one and ten minutes', () => {
    expect(observerReachPollIntervalMs(YOUNG_REACH_MS)).toBe(MID_POLL_MS);
    expect(observerReachPollIntervalMs(MID_REACH_MS - 1)).toBe(MID_POLL_MS);
  });

  it('stops polling after ten minutes', () => {
    expect(observerReachPollIntervalMs(MID_REACH_MS)).toBeNull();
    expect(observerReachPollIntervalMs(15 * 60_000)).toBeNull();
  });
});
