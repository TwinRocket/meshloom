import { useCallback, useState } from 'react';

import { api, formatApiError } from '../api';
import i18n from '../i18n';
import { toast } from '../components/ui/sonner';
import type { NotificationChannel, NotificationRules } from '../notifications/types';
import {
  withExceptionState,
  type ExceptionState,
} from '../components/settings/notifications/rulesShared';

function reportWriteFailure(err: unknown) {
  toast.error(i18n.t('settings.notifications.rulesSaveFailed'), {
    description: formatApiError(err, i18n.t),
  });
}

export interface ConversationNotificationsState {
  /** The enabled channels, in the server's order. */
  channels: NotificationChannel[];
  rules: NotificationRules | null;
  loading: boolean;
  error: unknown;
  load: () => Promise<void>;
  /** Moves one (conversation, channel) exception to `state`; resolves false when it failed. */
  setException: (stateKey: string, channelId: string, state: ExceptionState) => Promise<boolean>;
  /** Epoch seconds the global pause ends at, `null` resumes. */
  setPause: (until: number | null) => Promise<boolean>;
}

/**
 * The channels and rules behind the conversation bell. Nothing loads until `load()` (the
 * menu opening), so a session that never opens a bell never calls `/api/notifications`.
 */
export function useConversationNotifications(): ConversationNotificationsState {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [rules, setRules] = useState<NotificationRules | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextChannels, nextRules] = await Promise.all([
        api.getNotificationChannels(),
        api.getNotificationRules(),
      ]);
      setChannels(nextChannels.filter((channel) => channel.enabled));
      setRules(nextRules);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  const setException = useCallback(
    async (stateKey: string, channelId: string, state: ExceptionState) => {
      if (!rules) return false;
      // The server refuses unknown channel ids, so a write drops the orphans.
      const known = new Set(channels.map((c) => c.id));
      const stored = rules.conversations[stateKey] ?? {};
      const live = Object.fromEntries(Object.entries(stored).filter(([id]) => known.has(id)));
      try {
        setRules(
          await api.setNotificationConversationRule(
            stateKey,
            withExceptionState(live, channelId, state)
          )
        );
        return true;
      } catch (err) {
        reportWriteFailure(err);
        return false;
      }
    },
    [channels, rules]
  );

  const setPause = useCallback(async (until: number | null) => {
    try {
      setRules(await api.patchNotificationRules({ paused_until: until }));
      return true;
    } catch (err) {
      reportWriteFailure(err);
      return false;
    }
  }, []);

  return { channels, rules, loading, error, load, setException, setPause };
}
