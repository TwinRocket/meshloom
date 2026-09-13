import { useState, useCallback, useRef, useEffect } from 'react';
import { ApiError, api, formatApiError } from '../api';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import { loadPersistedDashboard, persistDashboard } from '../utils/repeaterDashboardStore';
import {
  forgetRepeaterSession,
  hasRememberedRepeaterSession,
  rememberRepeaterSession,
} from '../utils/repeaterSession';
import type {
  Conversation,
  PaneName,
  PaneState,
  RepeaterStatusResponse,
  RepeaterNeighborsResponse,
  RepeaterAclResponse,
  RepeaterNodeInfoResponse,
  RepeaterRadioSettingsResponse,
  RepeaterAdvertIntervalsResponse,
  RepeaterOwnerInfoResponse,
  RepeaterLppTelemetryResponse,
  RepeaterRegionsResponse,
  CommandResponse,
} from '../types';
import {
  buildServerLoginAttemptFromError,
  buildServerLoginAttemptFromResponse,
  type ServerLoginAttemptState,
} from '../utils/serverLoginState';

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;
const MAX_CACHED_REPEATERS = 20;

/** The API names panes in snake_case; the dashboard state uses camelCase. */
const API_PANE_TO_STATE: Record<string, PaneName | undefined> = {
  status: 'status',
  node_info: 'nodeInfo',
  neighbors: 'neighbors',
  acl: 'acl',
  radio_settings: 'radioSettings',
  advert_intervals: 'advertIntervals',
  owner_info: 'ownerInfo',
  lpp_telemetry: 'lppTelemetry',
  regions: 'regions',
};
const MAX_STORED_CONSOLE_ENTRIES = 100;

export const REPEATER_CONSOLE_HISTORY_KEY_PREFIX = 'meshloom-repeater-console-history';

interface ConsoleEntry {
  command: string;
  response: string;
  timestamp: number;
  outgoing: boolean;
}

function consoleHistoryStorageKey(publicKey: string): string {
  return `${REPEATER_CONSOLE_HISTORY_KEY_PREFIX}:${publicKey}`;
}

function isConsoleEntry(value: unknown): value is ConsoleEntry {
  if (!value || typeof value !== 'object') return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.command === 'string' &&
    typeof entry.response === 'string' &&
    typeof entry.timestamp === 'number' &&
    typeof entry.outgoing === 'boolean'
  );
}

function loadStoredConsoleHistory(publicKey: string): ConsoleEntry[] {
  try {
    const raw = localStorage.getItem(consoleHistoryStorageKey(publicKey));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isConsoleEntry).slice(-MAX_STORED_CONSOLE_ENTRIES);
  } catch {
    return [];
  }
}

function saveStoredConsoleHistory(publicKey: string, history: ConsoleEntry[]): void {
  try {
    localStorage.setItem(
      consoleHistoryStorageKey(publicKey),
      JSON.stringify(history.slice(-MAX_STORED_CONSOLE_ENTRIES))
    );
  } catch {
    // localStorage may be unavailable
  }
}

interface PaneData {
  status: RepeaterStatusResponse | null;
  nodeInfo: RepeaterNodeInfoResponse | null;
  neighbors: RepeaterNeighborsResponse | null;
  acl: RepeaterAclResponse | null;
  radioSettings: RepeaterRadioSettingsResponse | null;
  advertIntervals: RepeaterAdvertIntervalsResponse | null;
  ownerInfo: RepeaterOwnerInfoResponse | null;
  lppTelemetry: RepeaterLppTelemetryResponse | null;
  regions: RepeaterRegionsResponse | null;
}

interface RepeaterDashboardCacheEntry {
  loggedIn: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: PaneData;
  paneStates: Record<PaneName, PaneState>;
  consoleHistory: ConsoleEntry[];
}

const INITIAL_PANE_STATE: PaneState = { loading: false, attempt: 0, error: null, fetched_at: null };

function createInitialPaneStates(): Record<PaneName, PaneState> {
  return {
    status: { ...INITIAL_PANE_STATE },
    nodeInfo: { ...INITIAL_PANE_STATE },
    neighbors: { ...INITIAL_PANE_STATE },
    acl: { ...INITIAL_PANE_STATE },
    radioSettings: { ...INITIAL_PANE_STATE },
    advertIntervals: { ...INITIAL_PANE_STATE },
    ownerInfo: { ...INITIAL_PANE_STATE },
    lppTelemetry: { ...INITIAL_PANE_STATE },
    regions: { ...INITIAL_PANE_STATE },
  };
}

function createInitialPaneData(): PaneData {
  return {
    status: null,
    nodeInfo: null,
    neighbors: null,
    acl: null,
    radioSettings: null,
    advertIntervals: null,
    ownerInfo: null,
    lppTelemetry: null,
    regions: null,
  };
}

const repeaterDashboardCache = new Map<string, RepeaterDashboardCacheEntry>();

function getLoginToastTitle(status: string): string {
  switch (status) {
    case 'timeout':
      return i18n.t('toast.loginNotHeard');
    case 'error':
      return i18n.t('toast.loginNotConfirmed');
    default:
      return i18n.t('toast.repeaterLoginNotConfirmed');
  }
}

const PANE_LABEL_KEYS: Record<PaneName, string> = {
  status: 'repeater.telemetry',
  nodeInfo: 'repeater.nodeInfo',
  neighbors: 'repeater.neighbors',
  radioSettings: 'repeater.radioSettings',
  acl: 'repeater.acl',
  advertIntervals: 'repeater.advertIntervals',
  ownerInfo: 'repeater.ownerInfo',
  lppTelemetry: 'repeater.lppSensors',
  regions: 'repeater.regions',
};

function clonePaneData(data: PaneData): PaneData {
  return { ...data };
}

function normalizePaneStates(paneStates: Record<PaneName, PaneState>): Record<PaneName, PaneState> {
  return {
    status: { ...paneStates.status, loading: false },
    nodeInfo: { ...paneStates.nodeInfo, loading: false },
    neighbors: { ...paneStates.neighbors, loading: false },
    acl: { ...paneStates.acl, loading: false },
    radioSettings: { ...paneStates.radioSettings, loading: false },
    advertIntervals: { ...paneStates.advertIntervals, loading: false },
    ownerInfo: { ...paneStates.ownerInfo, loading: false },
    lppTelemetry: { ...paneStates.lppTelemetry, loading: false },
    regions: { ...paneStates.regions, loading: false },
  };
}

function cloneConsoleHistory(consoleHistory: ConsoleEntry[]): ConsoleEntry[] {
  return consoleHistory.map((entry) => ({ ...entry }));
}

function getCachedState(publicKey: string | null): RepeaterDashboardCacheEntry | null {
  if (!publicKey) return null;
  const cached = repeaterDashboardCache.get(publicKey);
  if (!cached) {
    // Cold cache — a reload, typically. Reopen on what the repeater last told us
    // rather than on nine empty panes.
    const persisted = loadPersistedDashboard<
      RepeaterDashboardCacheEntry['paneData'],
      RepeaterDashboardCacheEntry['paneStates']
    >(publicKey);
    if (!persisted) return null;
    return {
      loggedIn: persisted.loggedIn,
      loginError: null,
      lastLoginAttempt: null,
      paneData: clonePaneData(persisted.paneData),
      paneStates: normalizePaneStates(persisted.paneStates),
      // The console keeps its own store; going through the cache must not shadow it.
      consoleHistory: loadStoredConsoleHistory(publicKey),
    };
  }

  repeaterDashboardCache.delete(publicKey);
  repeaterDashboardCache.set(publicKey, cached);

  return {
    loggedIn: cached.loggedIn,
    loginError: cached.loginError,
    lastLoginAttempt: cached.lastLoginAttempt,
    paneData: clonePaneData(cached.paneData),
    paneStates: normalizePaneStates(cached.paneStates),
    consoleHistory: cloneConsoleHistory(cached.consoleHistory),
  };
}

function cacheState(publicKey: string, entry: RepeaterDashboardCacheEntry) {
  // Pane values only: the console history already has its own storage.
  persistDashboard(publicKey, {
    loggedIn: entry.loggedIn,
    paneData: entry.paneData,
    paneStates: entry.paneStates,
  });
  repeaterDashboardCache.delete(publicKey);
  repeaterDashboardCache.set(publicKey, {
    loggedIn: entry.loggedIn,
    loginError: entry.loginError,
    lastLoginAttempt: entry.lastLoginAttempt,
    paneData: clonePaneData(entry.paneData),
    paneStates: normalizePaneStates(entry.paneStates),
    consoleHistory: cloneConsoleHistory(entry.consoleHistory),
  });

  if (repeaterDashboardCache.size > MAX_CACHED_REPEATERS) {
    const lruKey = repeaterDashboardCache.keys().next().value as string | undefined;
    if (lruKey) {
      repeaterDashboardCache.delete(lruKey);
    }
  }
}

export function resetRepeaterDashboardCacheForTests() {
  repeaterDashboardCache.clear();
}

// Maps pane name to the API call
function fetchPaneData(publicKey: string, pane: PaneName) {
  switch (pane) {
    case 'status':
      return api.repeaterStatus(publicKey);
    case 'nodeInfo':
      return api.repeaterNodeInfo(publicKey);
    case 'neighbors':
      return api.repeaterNeighbors(publicKey);
    case 'acl':
      return api.repeaterAcl(publicKey);
    case 'radioSettings':
      return api.repeaterRadioSettings(publicKey);
    case 'advertIntervals':
      return api.repeaterAdvertIntervals(publicKey);
    case 'ownerInfo':
      return api.repeaterOwnerInfo(publicKey);
    case 'lppTelemetry':
      return api.repeaterLppTelemetry(publicKey);
    case 'regions':
      return api.repeaterRegions(publicKey);
  }
}

export interface UseRepeaterDashboardResult {
  loggedIn: boolean;
  loginLoading: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: PaneData;
  paneStates: Record<PaneName, PaneState>;
  consoleHistory: ConsoleEntry[];
  consoleLoading: boolean;
  login: (password: string) => Promise<void>;
  loginAsGuest: () => Promise<void>;
  resetLogin: () => void;
  refreshPane: (pane: PaneName) => Promise<void>;
  loadAll: () => Promise<void>;
  cancelLoadAll: () => void;
  queuedPanes: PaneName[];
  loadAllProgress: { done: number; total: number } | null;
  sendConsoleCommand: (command: string) => Promise<void>;
  sendZeroHopAdvert: () => Promise<void>;
  sendFloodAdvert: () => Promise<void>;
  rebootRepeater: () => Promise<void>;
  syncClock: () => Promise<void>;
}

interface UseRepeaterDashboardOptions {
  hasAdvertLocation?: boolean;
}

export function useRepeaterDashboard(
  activeConversation: Conversation | null,
  options: UseRepeaterDashboardOptions = {}
): UseRepeaterDashboardResult {
  const conversationId =
    activeConversation && activeConversation.type === 'contact' ? activeConversation.id : null;
  const cachedState = getCachedState(conversationId);

  // The in-memory cache does not survive a reload, which sent people back to the
  // login form on every refresh. Fall back to the remembered flag.
  const [loggedIn, setLoggedIn] = useState(
    cachedState?.loggedIn ?? hasRememberedRepeaterSession(conversationId)
  );
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(cachedState?.loginError ?? null);
  const [lastLoginAttempt, setLastLoginAttempt] = useState<ServerLoginAttemptState | null>(
    cachedState?.lastLoginAttempt ?? null
  );

  const [paneData, setPaneData] = useState<PaneData>(
    cachedState?.paneData ?? createInitialPaneData
  );
  const [paneStates, setPaneStates] = useState<Record<PaneName, PaneState>>(
    cachedState?.paneStates ?? createInitialPaneStates
  );
  const paneDataRef = useRef<PaneData>(cachedState?.paneData ?? createInitialPaneData());
  const paneStatesRef = useRef<Record<PaneName, PaneState>>(
    cachedState?.paneStates ?? createInitialPaneStates()
  );

  const [consoleHistory, setConsoleHistory] = useState<ConsoleEntry[]>(
    cachedState?.consoleHistory ?? (conversationId ? loadStoredConsoleHistory(conversationId) : [])
  );
  const [consoleLoading, setConsoleLoading] = useState(false);

  // Track which conversation we're operating on to avoid stale updates after
  // unmount. Initialised from activeConversation because the parent renders
  // <RepeaterDashboard key={id}>, so this hook only ever sees one conversation.
  const activeIdRef = useRef(activeConversation?.id ?? null);

  // Guard against setting state after unmount (retry timers firing late)
  const mountedRef = useRef(true);
  useEffect(() => {
    activeIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Hydrate from the server-side cache. This is a plain GET against the database:
  // it never reaches the radio, so the dashboard can open on what the repeater
  // last said — including the neighbours the map needs — instead of nine empty
  // panes and a mesh round trip. Panes already holding a value are left alone.
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;

    void api
      .repeaterPaneCache(conversationId)
      .then((cached) => {
        if (cancelled || !mountedRef.current || activeIdRef.current !== conversationId) return;
        const nextData = { ...paneDataRef.current };
        const nextStates = { ...paneStatesRef.current };
        let changed = false;

        for (const [apiPane, entry] of Object.entries(cached)) {
          const pane = API_PANE_TO_STATE[apiPane];
          if (!pane || nextData[pane] != null) continue;
          nextData[pane] = entry.data as never;
          nextStates[pane] = {
            ...nextStates[pane],
            loading: false,
            error: null,
            fetched_at: entry.fetched_at * 1000,
          };
          changed = true;
        }

        if (!changed) return;
        paneDataRef.current = nextData;
        paneStatesRef.current = nextStates;
        setPaneData(nextData);
        setPaneStates(nextStates);
      })
      .catch(() => {
        // No cache yet, or the contact is gone: the panes stay as they were.
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) return;
    cacheState(conversationId, {
      loggedIn,
      loginError,
      lastLoginAttempt,
      paneData,
      paneStates,
      consoleHistory,
    });
    saveStoredConsoleHistory(conversationId, consoleHistory);
  }, [
    consoleHistory,
    conversationId,
    loggedIn,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
  ]);

  useEffect(() => {
    paneDataRef.current = paneData;
  }, [paneData]);

  useEffect(() => {
    paneStatesRef.current = paneStates;
  }, [paneStates]);

  const getPublicKey = useCallback((): string | null => {
    if (!activeConversation || activeConversation.type !== 'contact') return null;
    return activeConversation.id;
  }, [activeConversation]);

  const login = useCallback(
    async (password: string) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;
      const method = password.trim().length > 0 ? 'password' : 'blank';

      setLoginLoading(true);
      setLoginError(null);
      try {
        const result = await api.repeaterLogin(publicKey, password);
        if (activeIdRef.current !== conversationId) return;
        setLastLoginAttempt(buildServerLoginAttemptFromResponse(method, result, 'repeater'));
        setLoggedIn(true);
        rememberRepeaterSession(publicKey);
        if (!result.authenticated) {
          const msg = result.message ?? i18n.t('repeater.loginNotConfirmed');
          setLoginError(msg);
          toast.error(getLoginToastTitle(result.status), { description: msg });
        }
      } catch (err) {
        if (activeIdRef.current !== conversationId) return;
        const msg = err instanceof Error ? err.message : i18n.t('toast.loginFailed');
        setLastLoginAttempt(buildServerLoginAttemptFromError(method, msg, 'repeater'));
        setLoggedIn(true);
        rememberRepeaterSession(publicKey);
        setLoginError(msg);
        toast.error(i18n.t('toast.loginRequestFailed'), {
          description: i18n.t('toast.loginRequestFailedDetail', { message: msg }),
        });
      } finally {
        if (activeIdRef.current === conversationId) {
          setLoginLoading(false);
        }
      }
    },
    [getPublicKey]
  );

  const loginAsGuest = useCallback(async () => {
    await login('');
  }, [login]);

  // Return to the login form (e.g. after a mistyped password).
  const resetLogin = useCallback(() => {
    setLoggedIn(false);
    setLoginError(null);
    setLastLoginAttempt(null);
    forgetRepeaterSession(getPublicKey());
  }, [getPublicKey]);

  const refreshPane = useCallback(
    async (pane: PaneName) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;

      if (pane === 'neighbors' && !options.hasAdvertLocation) {
        const nodeInfoState = paneStatesRef.current.nodeInfo;
        const nodeInfoData = paneDataRef.current.nodeInfo;
        const needsNodeInfoPrefetch =
          nodeInfoState.error !== null ||
          (nodeInfoState.fetched_at == null && nodeInfoData == null);

        if (needsNodeInfoPrefetch) {
          await refreshPane('nodeInfo');
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;
        }
      }

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (!mountedRef.current || activeIdRef.current !== conversationId) return;

        const loadingState = {
          loading: true,
          attempt,
          error: null,
          fetched_at: paneStatesRef.current[pane].fetched_at ?? null,
        };
        paneStatesRef.current = {
          ...paneStatesRef.current,
          [pane]: loadingState,
        };
        setPaneStates((prev) => ({
          ...prev,
          [pane]: loadingState,
        }));

        try {
          const data = await fetchPaneData(publicKey, pane);
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;

          paneDataRef.current = {
            ...paneDataRef.current,
            [pane]: data,
          };
          const successState = {
            loading: false,
            attempt,
            error: null,
            fetched_at: Date.now(),
          };
          paneStatesRef.current = {
            ...paneStatesRef.current,
            [pane]: successState,
          };

          setPaneData((prev) => ({ ...prev, [pane]: data }));
          setPaneStates((prev) => ({
            ...prev,
            [pane]: successState,
          }));
          return; // Success
        } catch (err) {
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;

          const msg = err instanceof Error ? err.message : i18n.t('toast.requestFailed');
          // 4xx means the request itself failed (e.g. 422 mesh timeout); retrying
          // just floods the mesh with duplicate requests.
          const retryable = !(err instanceof ApiError && err.status >= 400 && err.status < 500);

          if (attempt === MAX_RETRIES || !retryable) {
            const errorState = {
              loading: false,
              attempt,
              error: msg,
              fetched_at: paneStatesRef.current[pane].fetched_at ?? null,
            };
            paneStatesRef.current = {
              ...paneStatesRef.current,
              [pane]: errorState,
            };
            setPaneStates((prev) => ({
              ...prev,
              [pane]: errorState,
            }));
            toast.error(i18n.t('toast.fetchPaneFailed', { pane: i18n.t(PANE_LABEL_KEYS[pane]) }), {
              description: formatApiError(err, i18n.t) || msg,
            });
            return;
          } else {
            // Wait before retrying
            await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
          }
        }
      }
    },
    [getPublicKey, options.hasAdvertLocation]
  );

  // Load-all is serial by necessity (parallel calls just queue behind the radio
  // lock), so only one pane ever spins while the rest sit at "not fetched" —
  // indistinguishable from panes where nothing was asked for. Expose what is
  // queued, how far along the run is, and a way out: nine panes times three
  // attempts times a ten-second timeout is minutes of waiting.
  const [queuedPanes, setQueuedPanes] = useState<PaneName[]>([]);
  const [loadAllProgress, setLoadAllProgress] = useState<{ done: number; total: number } | null>(
    null
  );
  const cancelLoadAllRef = useRef(false);

  const cancelLoadAll = useCallback(() => {
    cancelLoadAllRef.current = true;
  }, []);

  const loadAll = useCallback(async () => {
    const panes: PaneName[] = [
      'status',
      'nodeInfo',
      'neighbors',
      'radioSettings',
      'acl',
      'advertIntervals',
      'ownerInfo',
      'lppTelemetry',
      'regions',
    ];
    const markQueued = (pending: PaneName[]) => {
      const pendingSet = new Set(pending);
      const next = { ...paneStatesRef.current };
      for (const pane of panes) {
        next[pane] = { ...next[pane], queued: pendingSet.has(pane) };
      }
      paneStatesRef.current = next;
      setPaneStates(next);
    };

    cancelLoadAllRef.current = false;
    setQueuedPanes(panes);
    markQueued(panes);
    setLoadAllProgress({ done: 0, total: panes.length });
    try {
      for (let i = 0; i < panes.length; i++) {
        // Stopping ends the queue; it cannot recall a request already on the air.
        if (cancelLoadAllRef.current || !mountedRef.current) break;
        const pending = panes.slice(i + 1);
        setQueuedPanes(pending);
        markQueued(pending);
        await refreshPane(panes[i]);
        setLoadAllProgress({ done: i + 1, total: panes.length });
      }
    } finally {
      setQueuedPanes([]);
      markQueued([]);
      setLoadAllProgress(null);
      cancelLoadAllRef.current = false;
    }
  }, [refreshPane]);

  const sendConsoleCommand = useCallback(
    async (command: string) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;

      const now = Math.floor(Date.now() / 1000);

      // Add outgoing command entry
      setConsoleHistory((prev) => [
        ...prev,
        { command, response: '', timestamp: now, outgoing: true },
      ]);

      setConsoleLoading(true);
      try {
        const result: CommandResponse = await api.sendRepeaterCommand(publicKey, command);
        if (activeIdRef.current !== conversationId) return;

        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: result.response,
            timestamp: result.sender_timestamp ?? now,
            outgoing: false,
          },
        ]);
      } catch (err) {
        if (activeIdRef.current !== conversationId) return;
        const msg = err instanceof Error ? err.message : i18n.t('toast.commandFailed');
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: i18n.t('repeater.consoleError', { message: msg }),
            timestamp: now,
            outgoing: false,
          },
        ]);
      } finally {
        if (activeIdRef.current === conversationId) {
          setConsoleLoading(false);
        }
      }
    },
    [getPublicKey]
  );

  const sendZeroHopAdvert = useCallback(async () => {
    await sendConsoleCommand('advert.zerohop');
  }, [sendConsoleCommand]);

  const sendFloodAdvert = useCallback(async () => {
    await sendConsoleCommand('advert');
  }, [sendConsoleCommand]);

  const rebootRepeater = useCallback(async () => {
    await sendConsoleCommand('reboot');
  }, [sendConsoleCommand]);

  const syncClock = useCallback(async () => {
    const epochSeconds = Math.floor(Date.now() / 1000);
    await sendConsoleCommand(`time ${epochSeconds}`);
  }, [sendConsoleCommand]);

  return {
    loggedIn,
    loginLoading,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
    consoleHistory,
    consoleLoading,
    login,
    loginAsGuest,
    resetLogin,
    refreshPane,
    loadAll,
    cancelLoadAll,
    queuedPanes,
    loadAllProgress,
    sendConsoleCommand,
    sendZeroHopAdvert,
    sendFloodAdvert,
    rebootRepeater,
    syncClock,
  };
}
