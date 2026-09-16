import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import { api } from '../api';
import i18n from '../i18n';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import type {
  Contact,
  PaneState,
  RepeaterAclResponse,
  RepeaterLppTelemetryResponse,
  RepeaterStatusResponse,
} from '../types';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { AclPane } from './repeater/RepeaterAclPane';
import { LppTelemetryPane } from './repeater/RepeaterLppTelemetryPane';
import { ConsolePane } from './repeater/RepeaterConsolePane';
import { RepeaterLogin } from './RepeaterLogin';
import { ServerLoginStatusBanner } from './ServerLoginStatusBanner';
import { useRememberedServerPassword } from '../hooks/useRememberedServerPassword';
import {
  buildServerLoginAttemptFromError,
  buildServerLoginAttemptFromResponse,
  type ServerLoginAttemptState,
} from '../utils/serverLoginState';

interface RoomServerPanelProps {
  contact: Contact;
  onAuthenticatedChange?: (authenticated: boolean) => void;
}

type RoomPaneKey = 'status' | 'acl' | 'lppTelemetry';

type RoomPaneData = {
  status: RepeaterStatusResponse | null;
  acl: RepeaterAclResponse | null;
  lppTelemetry: RepeaterLppTelemetryResponse | null;
};

type RoomPaneStates = Record<RoomPaneKey, PaneState>;

type ConsoleEntry = {
  command: string;
  response: string;
  timestamp: number;
  outgoing: boolean;
};

const INITIAL_PANE_STATE: PaneState = {
  loading: false,
  attempt: 0,
  error: null,
  fetched_at: null,
};

function createInitialPaneStates(): RoomPaneStates {
  return {
    status: { ...INITIAL_PANE_STATE },
    acl: { ...INITIAL_PANE_STATE },
    lppTelemetry: { ...INITIAL_PANE_STATE },
  };
}

function createInitialPaneData(): RoomPaneData {
  return { status: null, acl: null, lppTelemetry: null };
}

// ---------------------------------------------------------------------------
// In-memory LRU cache so room login state survives conversation switches
// ---------------------------------------------------------------------------

interface RoomCacheEntry {
  authenticated: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: RoomPaneData;
  paneStates: RoomPaneStates;
  consoleHistory: ConsoleEntry[];
}

const MAX_CACHED_ROOMS = 8;
const roomCache = new Map<string, RoomCacheEntry>();

function getCachedRoom(publicKey: string): RoomCacheEntry | null {
  const cached = roomCache.get(publicKey);
  if (!cached) return null;
  // Touch for LRU
  roomCache.delete(publicKey);
  roomCache.set(publicKey, cached);
  return {
    ...cached,
    paneData: { ...cached.paneData },
    paneStates: {
      status: { ...cached.paneStates.status, loading: false },
      acl: { ...cached.paneStates.acl, loading: false },
      lppTelemetry: { ...cached.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: cached.consoleHistory.map((e) => ({ ...e })),
  };
}

function setCachedRoom(publicKey: string, entry: RoomCacheEntry) {
  roomCache.delete(publicKey);
  roomCache.set(publicKey, {
    ...entry,
    paneData: { ...entry.paneData },
    paneStates: {
      status: { ...entry.paneStates.status, loading: false },
      acl: { ...entry.paneStates.acl, loading: false },
      lppTelemetry: { ...entry.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: entry.consoleHistory.map((e) => ({ ...e })),
  });
  if (roomCache.size > MAX_CACHED_ROOMS) {
    const lruKey = roomCache.keys().next().value as string | undefined;
    if (lruKey) roomCache.delete(lruKey);
  }
}

export function resetRoomCacheForTests() {
  roomCache.clear();
}

export function RoomServerPanel({ contact, onAuthenticatedChange }: RoomServerPanelProps) {
  const { t } = useTranslation();
  const {
    password,
    storedPassword,
    setPassword,
    rememberPassword,
    setRememberPassword,
    persistAfterLogin,
    forgetPassword,
  } = useRememberedServerPassword('room', contact.public_key);

  const cached = useMemo(() => getCachedRoom(contact.public_key), [contact.public_key]);

  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(cached?.loginError ?? null);
  const [authenticated, setAuthenticated] = useState(cached?.authenticated ?? false);
  const [lastLoginAttempt, setLastLoginAttempt] = useState<ServerLoginAttemptState | null>(
    cached?.lastLoginAttempt ?? null
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [paneData, setPaneData] = useState<RoomPaneData>(cached?.paneData ?? createInitialPaneData);
  const [paneStates, setPaneStates] = useState<RoomPaneStates>(
    cached?.paneStates ?? createInitialPaneStates
  );
  const [consoleHistory, setConsoleHistory] = useState<ConsoleEntry[]>(
    cached?.consoleHistory ?? []
  );
  const [consoleLoading, setConsoleLoading] = useState(false);

  // Persist to cache on every state change
  useEffect(() => {
    setCachedRoom(contact.public_key, {
      authenticated,
      loginError,
      lastLoginAttempt,
      paneData,
      paneStates,
      consoleHistory,
    });
  }, [
    contact.public_key,
    authenticated,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
    consoleHistory,
  ]);

  useEffect(() => {
    onAuthenticatedChange?.(authenticated);
  }, [authenticated, onAuthenticatedChange]);

  const refreshPane = useCallback(
    async <K extends RoomPaneKey>(pane: K, loader: () => Promise<RoomPaneData[K]>) => {
      setPaneStates((prev) => ({
        ...prev,
        [pane]: {
          ...prev[pane],
          loading: true,
          attempt: prev[pane].attempt + 1,
          error: null,
        },
      }));

      try {
        const data = await loader();
        setPaneData((prev) => ({ ...prev, [pane]: data }));
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            loading: false,
            attempt: prev[pane].attempt,
            error: null,
            fetched_at: Date.now(),
          },
        }));
      } catch (err) {
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            ...prev[pane],
            loading: false,
            error: err instanceof Error ? err.message : i18n.t('toast.unknownError'),
          },
        }));
      }
    },
    []
  );

  const performLogin = useCallback(
    async (nextPassword: string, method: 'password' | 'blank') => {
      if (loginLoading) return;

      setLoginLoading(true);
      setLoginError(null);
      try {
        const result = await api.roomLogin(contact.public_key, nextPassword);
        setLastLoginAttempt(buildServerLoginAttemptFromResponse(method, result, 'room'));
        setAuthenticated(true);
        if (result.authenticated) {
          toast.success(i18n.t('room.loginConfirmed'));
        } else {
          toast.warning(i18n.t('room.loginUnconfirmed'), {
            description: result.message ?? i18n.t('room.loginUnconfirmedFallback'),
          });
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : i18n.t('toast.unknownError');
        setLastLoginAttempt(buildServerLoginAttemptFromError(method, message, 'room'));
        setAuthenticated(true);
        setLoginError(message);
        toast.error(i18n.t('room.loginFailed'), {
          description: i18n.t('room.loginFailedDetail', { message }),
        });
      } finally {
        setLoginLoading(false);
      }
    },
    [contact.public_key, loginLoading]
  );

  const handleLogin = useCallback(
    async (nextPassword: string) => {
      await performLogin(nextPassword, 'password');
      persistAfterLogin(nextPassword);
    },
    [performLogin, persistAfterLogin]
  );

  // Log in once on open when we already know the password, so reopening a room
  // pulls the backlog without a manual click. The ref guard is deliberate: a
  // failed login must never retry, because every login is mesh traffic.
  const autoLoginFiredRef = useRef(false);
  useEffect(() => {
    if (autoLoginFiredRef.current || authenticated || loginLoading || !storedPassword) return;
    autoLoginFiredRef.current = true;
    void handleLogin(storedPassword);
  }, [storedPassword, authenticated, loginLoading, handleLogin]);

  const handleLoginAsGuest = useCallback(async () => {
    await performLogin('', 'blank');
    persistAfterLogin('');
  }, [performLogin, persistAfterLogin]);

  // Return to the login form (e.g. after a mistyped password).
  const handleReenterPassword = useCallback(() => {
    forgetPassword();
    setAuthenticated(false);
    setLoginError(null);
    setLastLoginAttempt(null);
  }, [forgetPassword]);

  const handleConsoleCommand = useCallback(
    async (command: string) => {
      setConsoleLoading(true);
      const timestamp = Date.now();
      setConsoleHistory((prev) => [
        ...prev,
        { command, response: command, timestamp, outgoing: true },
      ]);
      try {
        const response = await api.sendRepeaterCommand(contact.public_key, command);
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: response.response,
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : i18n.t('toast.unknownError');
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: i18n.t('room.consoleError', { message }),
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } finally {
        setConsoleLoading(false);
      }
    },
    [contact.public_key]
  );

  const panelTitle = useMemo(() => contact.name || contact.public_key.slice(0, 12), [contact]);
  const showLoginFailureState =
    lastLoginAttempt !== null && lastLoginAttempt.outcome !== 'confirmed';

  if (!authenticated) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex w-full max-w-sm flex-col gap-4">
          <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
            <Trans
              i18nKey="room.alphaWarning"
              components={{
                github: (
                  <a
                    href="https://github.com/TwinRocket/meshloom/issues"
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium underline underline-offset-2 hover:text-warning/80"
                  />
                ),
              }}
            />
          </div>
          <RepeaterLogin
            repeaterName={panelTitle}
            loading={loginLoading}
            error={loginError}
            password={password}
            onPasswordChange={setPassword}
            rememberPassword={rememberPassword}
            onRememberPasswordChange={setRememberPassword}
            onLogin={handleLogin}
            onLoginAsGuest={handleLoginAsGuest}
            description={t('room.loginDescription')}
            passwordPlaceholder={t('room.passwordPlaceholder')}
            guestLabel={t('room.loginGuest')}
          />
        </div>
      </div>
    );
  }

  return (
    <section className="border-b border-border bg-muted/20 px-4 py-3">
      <div className="space-y-3">
        {showLoginFailureState ? (
          <ServerLoginStatusBanner
            attempt={lastLoginAttempt}
            loading={loginLoading}
            canRetryPassword={password.trim().length > 0}
            onRetryPassword={() => handleLogin(password)}
            onRetryBlank={handleLoginAsGuest}
            onReenterPassword={handleReenterPassword}
            blankRetryLabel={t('room.retryExistingAccess')}
          />
        ) : null}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loginLoading}
            title={t('room.syncTitle')}
            onClick={() => performLogin(password, password.trim() ? 'password' : 'blank')}
          >
            {loginLoading ? t('room.syncing') : t('room.syncNow')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setAdvancedOpen((prev) => !prev)}
          >
            {advancedOpen ? t('room.hideTools') : t('room.showTools')}
          </Button>
        </div>
      </div>
      <Sheet open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <SheetContent side="right" className="w-full sm:max-w-4xl p-0 flex flex-col">
          <SheetHeader className="sr-only">
            <SheetTitle>{t('room.toolsTitle')}</SheetTitle>
            <SheetDescription>{t('room.toolsDescription')}</SheetDescription>
          </SheetHeader>
          <div className="border-b border-border px-4 py-3 pr-14">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">{t('room.toolsTitle')}</h2>
                <p className="text-sm text-muted-foreground">{panelTitle}</p>
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <div className="grid gap-3 xl:grid-cols-2">
              <TelemetryPane
                data={paneData.status}
                state={paneStates.status}
                onRefresh={() => refreshPane('status', () => api.roomStatus(contact.public_key))}
              />
              <AclPane
                data={paneData.acl}
                state={paneStates.acl}
                onRefresh={() => refreshPane('acl', () => api.roomAcl(contact.public_key))}
              />
              <LppTelemetryPane
                data={paneData.lppTelemetry}
                state={paneStates.lppTelemetry}
                onRefresh={() =>
                  refreshPane('lppTelemetry', () => api.roomLppTelemetry(contact.public_key))
                }
              />
              <ConsolePane
                history={consoleHistory}
                loading={consoleLoading}
                onSend={handleConsoleCommand}
              />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
