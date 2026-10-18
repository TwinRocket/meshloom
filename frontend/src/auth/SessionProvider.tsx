import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { Button } from '../components/ui/button';
import { onAuthRequired } from './authEvents';
import { IngressSignInPage } from './IngressSignInPage';
import { LoginPage } from './LoginPage';
import { ReloginDialog } from './ReloginDialog';
import { SetupPage } from './SetupPage';
import {
  ingressOnly,
  type AuthRole,
  type AuthSessionInfo,
  type AuthUser,
  type AuthVia,
} from './types';

interface SessionValue {
  user: AuthUser | null;
  role: AuthRole | null;
  via: AuthVia | null;
  /** The last `GET /auth/session` answer (the gate renders children only when signed in). */
  session: AuthSessionInfo | null;
  /** Ends the session on the server and goes back to the login page. */
  signOut: () => Promise<boolean>;
}

const EMPTY: SessionValue = {
  user: null,
  role: null,
  via: null,
  session: null,
  signOut: async () => false,
};

const SessionContext = createContext<SessionValue>(EMPTY);

/** Who is signed in. Outside a provider (tests, previews) it reports nobody. */
export function useSession(): SessionValue {
  return useContext(SessionContext);
}

type GateState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; session: AuthSessionInfo };

/**
 * The session gate: nothing under it mounts, so nothing loads, until the server knows
 * who is calling. A first run shows the setup page, an anonymous visit the login page;
 * a deployment whose Basic credentials already authenticate is simply signed in, and
 * sees neither. A session that ends while the app is open keeps the app mounted under a
 * re-login dialog, so navigation state and drafts survive.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<GateState>({ status: 'loading' });
  const [expired, setExpired] = useState(false);

  const load = useCallback(async () => {
    try {
      const session = await api.getAuthSession();
      setState({ status: 'ready', session });
      return session;
    } catch (err) {
      setState({ status: 'error', message: formatApiError(err, t) });
      return null;
    }
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    api.getAuthSession().then(
      (session) => {
        if (!cancelled) setState({ status: 'ready', session });
      },
      (err: unknown) => {
        if (!cancelled) setState({ status: 'error', message: formatApiError(err, t) });
      }
    );
    return () => {
      cancelled = true;
    };
  }, [t]);

  const authenticated = state.status === 'ready' && state.session.authenticated;
  // Home Assistant signs ingress users in on every request: there is no Meshloom
  // session to renew, so a lost identity reloads the gate instead of asking a password.
  const viaIngress = state.status === 'ready' && state.session.via === 'ingress';

  // A 401 on an app call: the session is gone (re-login dialog), or the instance has no
  // account at all (a wiped database: back to the setup page).
  useEffect(() => {
    if (!authenticated) return;
    return onAuthRequired((code) => {
      if (code === 'auth_setup_required' || viaIngress) {
        setExpired(false);
        void load();
      } else {
        setExpired(true);
      }
    });
  }, [authenticated, viaIngress, load]);

  // A reopened PWA is usually long past its session: ask on the way back instead of
  // letting the first thing the reader does be the thing that fails.
  useEffect(() => {
    if (!authenticated) return;
    const check = () => {
      if (document.visibilityState !== 'visible') return;
      api.getAuthSession().then(
        (session) => {
          if (session.authenticated) return;
          if (viaIngress) setState({ status: 'ready', session });
          else setExpired(true);
        },
        () => {
          // Unreachable server: not a lost session.
        }
      );
    };
    document.addEventListener('visibilitychange', check);
    return () => document.removeEventListener('visibilitychange', check);
  }, [authenticated, viaIngress]);

  /** Resolves true when the session ended; false when the server still knows us. */
  const signOut = useCallback(async () => {
    try {
      await api.authLogout();
    } catch {
      // Whatever the server says, the reload below tells whether we are still signed in.
    }
    setExpired(false);
    const session = await load();
    return !session?.authenticated;
  }, [load]);

  const value = useMemo<SessionValue>(() => {
    if (state.status !== 'ready' || !state.session.authenticated || !state.session.user) {
      return { ...EMPTY, signOut };
    }
    return {
      user: state.session.user,
      role: state.session.user.role,
      via: state.session.via ?? null,
      session: state.session,
      signOut,
    };
  }, [state, signOut]);

  if (state.status === 'loading') {
    return (
      <div
        role="status"
        className="flex min-h-dvh items-center justify-center bg-background text-sm text-muted-foreground"
      >
        {t('auth.loading')}
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div role="alert" className="max-w-sm space-y-3 text-center text-sm">
          <p>{t('auth.sessionLoadFailed')}</p>
          <p className="text-muted-foreground">{state.message}</p>
          <Button
            type="button"
            onClick={() => {
              setState({ status: 'loading' });
              void load();
            }}
          >
            {t('auth.retry')}
          </Button>
        </div>
      </div>
    );
  }

  const { session } = state;
  if (!session.authenticated && ingressOnly(session)) {
    return (
      <IngressSignInPage
        onRetry={() => {
          setState({ status: 'loading' });
          void load();
        }}
      />
    );
  }
  if (session.setup_required) {
    return (
      <SetupPage
        setupCodeRequired={session.setup_code_required}
        onDone={(next) => setState({ status: 'ready', session: next })}
      />
    );
  }
  if (!session.authenticated) {
    return (
      <LoginPage
        session={session}
        onDone={(next) => setState({ status: 'ready', session: next })}
      />
    );
  }

  return (
    <SessionContext.Provider value={value}>
      {children}
      {expired && (
        <ReloginDialog
          username={session.user?.username ?? ''}
          previousUserId={session.user?.id ?? null}
          onDone={(next) => {
            setExpired(false);
            setState({ status: 'ready', session: next });
          }}
        />
      )}
    </SessionContext.Provider>
  );
}
