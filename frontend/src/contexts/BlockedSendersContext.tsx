import { createContext, useContext, useMemo, type ReactNode } from 'react';

interface BlockedSendersValue {
  blockedKeys: readonly string[];
  blockedNames: readonly string[];
}

const EMPTY: BlockedSendersValue = { blockedKeys: [], blockedNames: [] };

const BlockedSendersContext = createContext<BlockedSendersValue>(EMPTY);

/**
 * The block lists (`blocked_keys`, `blocked_names` of the settings) for message
 * rendering: a shared image from a blocked sender never loads without a tap.
 */
export function BlockedSendersProvider({
  blockedKeys,
  blockedNames,
  children,
}: BlockedSendersValue & { children: ReactNode }) {
  const value = useMemo(() => ({ blockedKeys, blockedNames }), [blockedKeys, blockedNames]);
  return <BlockedSendersContext.Provider value={value}>{children}</BlockedSendersContext.Provider>;
}

export function useBlockedSenders(): BlockedSendersValue {
  return useContext(BlockedSendersContext);
}
