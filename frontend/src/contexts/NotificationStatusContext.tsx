import { createContext, useContext, type ReactNode } from 'react';

import type { NotificationChannelStatus } from '../types';

const NotificationStatusContext = createContext<Record<string, NotificationChannelStatus>>({});

/**
 * `health.notification_statuses` (shown to administrators only, in the bell; kept live by the `health` WebSocket event),
 * for the status dots of the conversation bell.
 */
export function NotificationStatusProvider({
  statuses,
  capped = false,
  failing = false,
  children,
}: {
  statuses: Record<string, NotificationChannelStatus> | undefined;
  /** `health.notifications_capped`: the global hourly cap was reached. */
  capped?: boolean;
  /** `health.notifications_failing`: a channel keeps failing, is paused or cannot read a secret. */
  failing?: boolean;
  children: ReactNode;
}) {
  return (
    <NotificationStatusContext.Provider value={statuses ?? EMPTY}>
      <NotificationsCappedContext.Provider value={capped}>
        <NotificationsFailingContext.Provider value={failing}>
          {children}
        </NotificationsFailingContext.Provider>
      </NotificationsCappedContext.Provider>
    </NotificationStatusContext.Provider>
  );
}

const NotificationsCappedContext = createContext(false);

/** Whether the notification dispatcher hit its global hourly cap (health, admin banner). */
export function useNotificationsCapped(): boolean {
  return useContext(NotificationsCappedContext);
}

const NotificationsFailingContext = createContext(false);

/** Whether a notification channel needs a look (health, kept live by the `health` event). */
export function useNotificationsFailing(): boolean {
  return useContext(NotificationsFailingContext);
}

const EMPTY: Record<string, NotificationChannelStatus> = {};

export function useNotificationStatuses(): Record<string, NotificationChannelStatus> {
  return useContext(NotificationStatusContext);
}
