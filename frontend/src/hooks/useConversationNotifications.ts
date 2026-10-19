import { useCallback, useSyncExternalStore } from 'react';

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

interface Snapshot {
  /** The enabled channels, in the server's order. */
  channels: NotificationChannel[];
  rules: NotificationRules | null;
  loading: boolean;
  error: unknown;
}

// One cache for every bell on screen (the header's and the list rows'): a conversation switch
// remounts the header, and must not refetch.
let snapshot: Snapshot = { channels: [], rules: null, loading: false, error: null };
let inflight: Promise<void> | null = null;
let stale = false;
const listeners = new Set<() => void>();

function publish(next: Partial<Snapshot>) {
  snapshot = { ...snapshot, ...next };
  listeners.forEach((listener) => listener());
}

function fetchAll(): Promise<void> {
  if (inflight) return inflight;
  stale = false;
  publish({ loading: true });
  inflight = (async () => {
    try {
      const [channels, rules] = await Promise.all([
        api.getNotificationChannels(),
        api.getNotificationRules(),
      ]);
      publish({ channels: channels.filter((c) => c.enabled), rules, error: null });
    } catch (err) {
      publish({ error: err });
    } finally {
      inflight = null;
      publish({ loading: false });
      // A change that arrived mid-flight made this answer stale.
      if (stale) void fetchAll();
    }
  })();
  return inflight;
}

/**
 * Loads once, then serves the cache. `force` refetches (the bell does it when it opens).
 */
function load(force = false): Promise<void> {
  if (inflight) {
    if (force) stale = true;
    return inflight;
  }
  if (snapshot.rules && !force) return Promise.resolve();
  return fetchAll();
}

/**
 * A channel was created, changed or deleted (`notification_channel_changed`): the cached list
 * is wrong. Refetches at once when a bell has loaded it, else drops it for the next bell.
 */
export function invalidateConversationNotifications(): void {
  if (inflight) {
    stale = true;
    return;
  }
  if (snapshot.rules) {
    void fetchAll();
  }
}

/** Empties the cache (sign-out, tests). */
export function resetConversationNotifications(): void {
  inflight = null;
  stale = false;
  snapshot = { channels: [], rules: null, loading: false, error: null };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export interface ConversationNotificationsState extends Snapshot {
  /** Loads on first use; `force` refetches. */
  load: (force?: boolean) => Promise<void>;
  /** Moves one (conversation, channel) exception to `state`; resolves false when it failed. */
  setException: (stateKey: string, channelId: string, state: ExceptionState) => Promise<boolean>;
  /** Epoch seconds the global pause ends at, `null` resumes. */
  setPause: (until: number | null) => Promise<boolean>;
}

/**
 * The channels and rules behind the conversation bell, from one cache shared by every bell:
 * fetched on first use, replaced by the answer of each write, refetched when a bell opens
 * and when `notification_channel_changed` arrives. Nothing is fetched before a bell mounts,
 * so a session without a bell (any non-admin) never calls `/api/notifications`.
 */
export function useConversationNotifications(): ConversationNotificationsState {
  const state = useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => snapshot
  );

  const setException = useCallback(
    async (stateKey: string, channelId: string, next: ExceptionState) => {
      const { rules, channels } = snapshot;
      if (!rules) return false;
      // The server refuses unknown channel ids, so a write drops the orphans.
      const known = new Set(channels.map((c) => c.id));
      const stored = rules.conversations[stateKey] ?? {};
      const live = Object.fromEntries(Object.entries(stored).filter(([id]) => known.has(id)));
      try {
        publish({
          rules: await api.setNotificationConversationRule(
            stateKey,
            withExceptionState(live, channelId, next)
          ),
        });
        return true;
      } catch (err) {
        reportWriteFailure(err);
        return false;
      }
    },
    []
  );

  const setPause = useCallback(async (until: number | null) => {
    try {
      publish({ rules: await api.patchNotificationRules({ paused_until: until }) });
      return true;
    } catch (err) {
      reportWriteFailure(err);
      return false;
    }
  }, []);

  return { ...state, load, setException, setPause };
}
