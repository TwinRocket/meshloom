import { useCallback, useEffect, useState } from 'react';

import { api } from '../../../api';
import type {
  NotificationChannel,
  NotificationEventInfo,
  NotificationRules,
  NotificationTypeInfo,
} from '../../../notifications/types';

export interface NotificationChannelsState {
  channels: NotificationChannel[];
  types: NotificationTypeInfo[];
  rules: NotificationRules | null;
  events: NotificationEventInfo[];
  loading: boolean;
  error: unknown;
  reload: () => Promise<void>;
}

/** Channels, their type schemas, the rules and the event catalogue, loaded together. */
export function useNotificationChannels(): NotificationChannelsState {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [types, setTypes] = useState<NotificationTypeInfo[]>([]);
  const [rules, setRules] = useState<NotificationRules | null>(null);
  const [events, setEvents] = useState<NotificationEventInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const reload = useCallback(async () => {
    try {
      const [nextChannels, nextTypes, nextRules, nextEvents] = await Promise.all([
        api.getNotificationChannels(),
        api.getNotificationTypes(),
        api.getNotificationRules(),
        api.getNotificationEvents(),
      ]);
      setChannels(nextChannels);
      setTypes(nextTypes);
      setRules(nextRules);
      setEvents(nextEvents.events);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    void reload();
  }, [reload]);

  return { channels, types, rules, events, loading, error, reload };
}
