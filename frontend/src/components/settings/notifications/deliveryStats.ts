import type { NotificationDeliveries, NotificationDelivery } from '../../../notifications/types';

const DAY_S = 24 * 3600;

/** Journal statuses that are not a send, with the badge colour of each. */
export const DELIVERY_STATUS_CLASS: Record<string, string> = {
  failed: 'bg-destructive/10 text-destructive',
  rate_limited: 'bg-warning/10 text-warning',
  quota_exceeded: 'bg-warning/10 text-warning',
  aggregated: 'bg-primary/10 text-primary',
  suppressed_quiet: 'bg-primary/10 text-primary',
  paused: 'bg-muted text-muted-foreground',
  aborted: 'bg-muted text-muted-foreground',
};

export interface ChannelDeliveryStats {
  /** Sends in the last 24 hours (the hourly counters). */
  sent24h: number;
  /** Notifications held by the quiet hours in the last 24 hours. */
  suppressed: number;
  /** Notifications folded into a summary (`aggregated`) in the last 24 hours. */
  grouped: number;
}

export function sentLast24h(data: Pick<NotificationDeliveries, 'counters' | 'totals'>): number {
  return data.totals?.sent ?? data.counters.reduce((sum, row) => sum + row.sent, 0);
}

function inLast24h(row: NotificationDelivery, nowS: number): boolean {
  return row.created_at >= nowS - DAY_S;
}

export function deliveryStats(
  data: NotificationDeliveries,
  nowS: number = Date.now() / 1000
): ChannelDeliveryStats {
  const { totals } = data;
  if (totals) {
    // Exact figures of the server (the journal keeps only 200 rows per channel).
    return {
      sent24h: sentLast24h(data),
      suppressed: totals.suppressed_quiet,
      grouped: totals.aggregated,
    };
  }
  const recent = data.deliveries.filter((row) => inLast24h(row, nowS));
  return {
    sent24h: sentLast24h(data),
    suppressed: recent.filter((row) => row.status === 'suppressed_quiet').length,
    grouped: recent.filter((row) => row.status === 'aggregated').length,
  };
}
