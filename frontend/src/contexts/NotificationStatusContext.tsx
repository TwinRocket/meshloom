import { createContext, useContext, type ReactNode } from 'react';

import type { NotificationChannelStatus } from '../types';

const NotificationStatusContext = createContext<Record<string, NotificationChannelStatus>>({});

/**
 * `health.notification_statuses` (admin only, kept live by the `health` WebSocket event),
 * for the status dots of the conversation bell.
 */
export function NotificationStatusProvider({
  statuses,
  children,
}: {
  statuses: Record<string, NotificationChannelStatus> | undefined;
  children: ReactNode;
}) {
  return (
    <NotificationStatusContext.Provider value={statuses ?? EMPTY}>
      {children}
    </NotificationStatusContext.Provider>
  );
}

const EMPTY: Record<string, NotificationChannelStatus> = {};

export function useNotificationStatuses(): Record<string, NotificationChannelStatus> {
  return useContext(NotificationStatusContext);
}
