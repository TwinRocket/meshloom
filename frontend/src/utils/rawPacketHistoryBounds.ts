import type { RawPacket } from '../types';

export const HISTORY_PERIODS = ['1h', '24h', '7d', 'custom'] as const;
export type HistoryPeriod = (typeof HISTORY_PERIODS)[number];

export const HISTORY_PERIOD_SECONDS: Record<Exclude<HistoryPeriod, 'custom'>, number> = {
  '1h': 3600,
  '24h': 86_400,
  '7d': 7 * 86_400,
};

/** How many of the most recent live packets feed the server clock offset estimate. */
const OFFSET_SAMPLE_SIZE = 15;

/**
 * Estimate how far the browser clock is ahead of the server clock, in whole seconds
 * (negative when the server is ahead). The server stamps a live packet with its own
 * wall clock on receipt, and `recordRawPacket` stamps it again with the browser
 * clock (`received_at_ms`), so the difference is the clock skew plus a few ms of
 * transport. Uses the median over the most recent live packets; returns 0 when no
 * live packet is buffered (history-replayed packets have no receipt time).
 */
export function estimateServerClockOffsetSec(packets: readonly RawPacket[]): number {
  return measureServerClockOffsetSec(packets) ?? 0;
}

/** Like `estimateServerClockOffsetSec`, but null when no live packet is buffered. */
export function measureServerClockOffsetSec(packets: readonly RawPacket[]): number | null {
  const samples: number[] = [];
  for (let i = packets.length - 1; i >= 0 && samples.length < OFFSET_SAMPLE_SIZE; i -= 1) {
    const packet = packets[i];
    if (packet.received_at_ms === undefined || !Number.isFinite(packet.timestamp)) continue;
    samples.push(Math.floor(packet.received_at_ms / 1000) - packet.timestamp);
  }
  if (samples.length === 0) return null;
  samples.sort((a, b) => a - b);
  return samples[Math.floor(samples.length / 2)];
}

function toUnixSeconds(localValue: string): number | undefined {
  if (!localValue) return undefined;
  const ms = new Date(localValue).getTime();
  if (Number.isNaN(ms)) return undefined;
  return Math.floor(ms / 1000);
}

/**
 * Bounds for the history replay query, in server-clock Unix seconds: the server
 * filters on its stored `timestamp`. A preset ("last 1h") starts at the estimated
 * server now minus the period and has no upper bound, so a skewed server clock
 * neither empties the range (server behind) nor cuts off the latest packets
 * (server ahead). A custom range is absolute wall-clock time and is sent as typed.
 */
export function resolveHistoryBounds(
  period: HistoryPeriod,
  customSince: string,
  customUntil: string,
  serverClockOffsetSec: number = 0,
  browserNowMs: number = Date.now()
): { since?: number; until?: number; invalid: boolean } {
  if (period === 'custom') {
    const since = toUnixSeconds(customSince);
    const until = toUnixSeconds(customUntil);
    return {
      since,
      until,
      invalid: since !== undefined && until !== undefined && since > until,
    };
  }
  const serverNow = Math.floor(browserNowMs / 1000) - serverClockOffsetSec;
  return { since: serverNow - HISTORY_PERIOD_SECONDS[period], invalid: false };
}
