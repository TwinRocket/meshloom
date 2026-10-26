import { createContext, useContext, useMemo, type ReactNode } from 'react';

interface BlockedSendersValue {
  blockedKeys: readonly string[];
  blockedNames: readonly string[];
  /** False until the settings are loaded: the lists are not known yet, so nothing auto-loads. */
  loaded: boolean;
}

const EMPTY: BlockedSendersValue = { blockedKeys: [], blockedNames: [], loaded: false };

const BlockedSendersContext = createContext<BlockedSendersValue>(EMPTY);

/**
 * The block lists (`blocked_keys`, `blocked_names` of the settings) for message
 * rendering: a shared image from a blocked sender never loads without a tap, nor any
 * shared image before the lists are known (`loaded` false, also outside a provider).
 */
export function BlockedSendersProvider({
  blockedKeys,
  blockedNames,
  loaded = true,
  children,
}: Omit<BlockedSendersValue, 'loaded'> & { loaded?: boolean; children: ReactNode }) {
  const value = useMemo(
    () => ({ blockedKeys, blockedNames, loaded }),
    [blockedKeys, blockedNames, loaded]
  );
  return <BlockedSendersContext.Provider value={value}>{children}</BlockedSendersContext.Provider>;
}

export function useBlockedSenders(): BlockedSendersValue {
  return useContext(BlockedSendersContext);
}
