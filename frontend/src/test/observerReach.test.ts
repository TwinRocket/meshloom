import { describe, expect, it } from 'vitest';

import type { Message } from '../types';
import {
  isObserverReachEligible,
  isOutgoingReachReady,
  messageAgeMs,
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

describe('messageAgeMs and server clock skew', () => {
  // received_at is stamped by the server clock; nowMs comes from the browser.
  const browserNowMs = 1_800_000_000_000;
  const browserNowSec = browserNowMs / 1000;

  it('measures a just-received message as zero old with the server 2 min behind', () => {
    const msg = message({ received_at: browserNowSec - 120 });
    expect(messageAgeMs(msg, browserNowMs)).toBe(120_000);
    expect(messageAgeMs(msg, browserNowMs, 120)).toBe(0);
    expect(observerReachPollIntervalMs(messageAgeMs(msg, browserNowMs, 120))).toBe(YOUNG_POLL_MS);
  });

  it('measures a just-received message as zero old with the server 15 min ahead', () => {
    const msg = message({ received_at: browserNowSec + 900 });
    expect(messageAgeMs(msg, browserNowMs, -900)).toBe(0);
    expect(messageAgeMs(msg, browserNowMs + 61_000, -900)).toBe(61_000);
    expect(observerReachPollIntervalMs(messageAgeMs(msg, browserNowMs + 61_000, -900))).toBe(
      MID_POLL_MS
    );
  });

  it('treats an outgoing message as ready at once when the server clock is ahead', () => {
    const msg = message({ outgoing: true, received_at: browserNowSec + 300 });
    expect(isOutgoingReachReady(msg, browserNowMs)).toBe(false);
    expect(isOutgoingReachReady(msg, browserNowMs, -300)).toBe(true);
  });
});
