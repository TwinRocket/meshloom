import { useEffect, useState } from 'react';

import { api } from '../../../api';
import type { NotificationChannel } from '../../../notifications/types';
import { deliveryStats, type ChannelDeliveryStats } from './deliveryStats';

/**
 * Per-channel counters for the cards (sent in the last 24 h, held by quiet hours, grouped),
 * from `GET /notifications/deliveries?channel=` with the whole journal (200 rows). A failed
 * read leaves the card without counters rather than showing an error on every card.
 * `version` changes (a reload of the channels) refetch.
 */
export function useDeliveryStats(
  channels: NotificationChannel[],
  version: unknown
): Record<string, ChannelDeliveryStats> {
  const [stats, setStats] = useState<Record<string, ChannelDeliveryStats>>({});
  const ids = channels.map((c) => c.id).join('\n');

  useEffect(() => {
    let cancelled = false;
    const wanted = ids ? ids.split('\n') : [];
    void Promise.all(
      wanted.map(async (id) => {
        try {
          return [
            id,
            deliveryStats(await api.getNotificationDeliveries({ channel: id, limit: 200 })),
          ] as const;
        } catch {
          return null;
        }
      })
    ).then((rows) => {
      if (cancelled) return;
      setStats(Object.fromEntries(rows.filter((r) => r !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [ids, version]);

  return stats;
}
