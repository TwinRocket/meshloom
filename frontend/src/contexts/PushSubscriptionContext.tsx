import { createContext, useContext, type ReactNode } from 'react';
import { usePushSubscription, type PushSubscriptionState } from '../hooks/usePushSubscription';

const noopAsync = async () => {};
const noopAsyncNull = async () => null;

const defaultState: PushSubscriptionState = {
  isSupported: false,
  isSubscribed: false,
  currentSubscriptionId: null,
  allSubscriptions: [],
  preferences: null,
  overrideEntries: [],
  loading: false,
  subscribe: noopAsyncNull,
  unsubscribe: noopAsync,
  isConversationPushEnabled: () => false,
  isConversationMediaEnabled: () => false,
  setConversationOverride: noopAsync,
  patchPreferences: noopAsync,
  deleteSubscription: noopAsync,
  testPush: noopAsync,
  refreshSubscriptions: async () => [],
};

const PushSubscriptionContext = createContext<PushSubscriptionState>(defaultState);

export function PushSubscriptionProvider({ children }: { children: ReactNode }) {
  const push = usePushSubscription();
  return (
    <PushSubscriptionContext.Provider value={push}>{children}</PushSubscriptionContext.Provider>
  );
}

export function usePush(): PushSubscriptionState {
  return useContext(PushSubscriptionContext);
}
