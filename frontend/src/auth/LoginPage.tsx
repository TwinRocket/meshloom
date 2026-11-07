import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, LogIn, Mail, TriangleAlert } from 'lucide-react';

import { api, formatApiError } from '../api';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { AuthLayout } from './AuthLayout';
import { takeMagicLink } from './magicLink';
import { appBase, startOidcSignIn, takeOidcReturn } from './oidc';
import {
  formatPasskeyError,
  passkeyLoginAvailable,
  passkeyUnavailableHere,
  signInWithPasskey,
} from './passkeys';
import { SecondFactorStep } from './SecondFactorStep';
import {
  behindUntrustedHttpsProxy,
  noSessionReason,
  type AuthSessionInfo,
  type SecondFactorChallenge,
} from './types';

/** The sign-in form fields, shared with the re-login dialog. */
export function LoginForm({
  initialUsername = '',
  autoFocusPassword = false,
  passkey = false,
  oidc,
  initialChallenge = null,
  initialError = null,
  magicLink = false,
  magicLinkToken = null,
  onDone,
}: {
  initialUsername?: string;
  autoFocusPassword?: boolean;
  /** Offer "Sign in with a passkey" (the server and the browser both support it). */
  passkey?: boolean;
  /** Offer "Sign in with <label>" (the server's `methods.oidc`); the label may be empty. */
  oidc?: { label?: string };
  /** Back from the OpenID Connect provider: the account owes its second factor. */
  initialChallenge?: SecondFactorChallenge | null;
  /** Back from the OpenID Connect provider with a refusal, already translated. */
  initialError?: string | null;
  /** Offer "E-mail me a sign-in link" (the server's `methods.magic_link`). */
  magicLink?: boolean;
  /** Opened from a sign-in link: its token, traded for a session at once. */
  magicLinkToken?: string | null;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t, i18n } = useTranslation();
  const [username, setUsername] = useState(initialUsername);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  // The password checked and the account owes a code: the second step replaces the form.
  const [challenge, setChallenge] = useState<SecondFactorChallenge | null>(initialChallenge);
  // "E-mail me a sign-in link": the form asks for the username only; `linkSent` after.
  const [linkMode, setLinkMode] = useState(false);
  const [linkSent, setLinkSent] = useState(false);
  const linkTried = useRef(false);

  // Opened from a sign-in link: trade its token once (a StrictMode double effect too).
  useEffect(() => {
    if (!magicLinkToken || linkTried.current) return;
    linkTried.current = true;
    setBusy(true);
    api
      .verifyMagicLink(magicLinkToken)
      .then((session) => {
        if (!session.authenticated && session.second_factor) {
          setChallenge(session.second_factor);
          setBusy(false);
          return;
        }
        const reason = noSessionReason(session);
        if (reason) {
          setError(t(`errors.${reason}`));
          setBusy(false);
          return;
        }
        onDone(session);
      })
      .catch((err) => {
        setError(formatApiError(err, t));
        setBusy(false);
      });
  }, [magicLinkToken, onDone, t]);

  async function submitLink(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.requestMagicLink({
        username: username.trim(),
        remember,
        base: appBase(),
        lang: i18n.language,
      });
      setLinkSent(true);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  }

  const restart = useCallback((reason?: string) => {
    setChallenge(null);
    setBusy(false);
    setError(reason ?? null);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.authLogin({ username: username.trim(), password, remember });
      if (!session.authenticated && session.second_factor) {
        setChallenge(session.second_factor);
        setPassword('');
        setBusy(false);
        return;
      }
      const reason = noSessionReason(session);
      if (reason) {
        setError(t(`errors.${reason}`));
        setPassword('');
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      setError(formatApiError(err, t));
      setPassword('');
      setBusy(false);
    }
  }

  async function submitPasskey() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await signInWithPasskey(remember);
      const reason = noSessionReason(session);
      if (reason) {
        setError(t(`errors.${reason}`));
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      setError(formatPasskeyError(err, t));
      setBusy(false);
    }
  }

  if (challenge) {
    return <SecondFactorStep challenge={challenge} onDone={onDone} onRestart={restart} />;
  }

  if (linkMode) {
    return (
      <form onSubmit={submitLink} className="space-y-4" noValidate>
        <p className="text-sm text-muted-foreground">{t('auth.magicLink.help')}</p>
        <div className="space-y-1.5">
          <Label htmlFor="auth-link-username">{t('auth.username')}</Label>
          <Input
            id="auth-link-username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              setLinkSent(false);
            }}
          />
        </div>
        {linkSent && (
          <p role="status" className="text-sm text-foreground">
            {t('auth.magicLink.sent')}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={busy || !username.trim()}>
          <Mail className="mr-2 h-4 w-4" aria-hidden="true" />
          {t(linkSent ? 'auth.magicLink.sendAgain' : 'auth.magicLink.send')}
        </Button>
        <button
          type="button"
          className="text-sm text-foreground underline underline-offset-2 hover:text-primary"
          onClick={() => {
            setLinkMode(false);
            setLinkSent(false);
            setError(null);
          }}
        >
          {t('auth.magicLink.back')}
        </button>
      </form>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div className="space-y-1.5">
        <Label htmlFor="auth-username">{t('auth.username')}</Label>
        <Input
          id="auth-username"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoFocus={!autoFocusPassword}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="auth-password">{t('auth.password')}</Label>
        <Input
          id="auth-password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus={autoFocusPassword}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>
      <div className="flex items-start gap-2">
        <Checkbox
          id="auth-remember"
          className="mt-0.5"
          checked={remember}
          onCheckedChange={(value) => setRemember(value === true)}
          aria-describedby="auth-remember-hint"
        />
        <div className="space-y-0.5">
          <Label htmlFor="auth-remember" className="font-normal">
            {t('auth.login.remember')}
          </Label>
          <p id="auth-remember-hint" className="text-xs text-muted-foreground">
            {t('auth.login.rememberHint')}
          </p>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={busy || !username.trim() || !password}>
        {t('auth.login.submit')}
      </Button>
      {(passkey || oidc || magicLink) && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden="true">
          <span className="h-px flex-1 bg-border" />
          {t('auth.passkey.or')}
          <span className="h-px flex-1 bg-border" />
        </div>
      )}
      {passkey && (
        <>
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={busy}
            onClick={() => void submitPasskey()}
          >
            <KeyRound className="mr-2 h-4 w-4" aria-hidden="true" />
            {t('auth.passkey.signIn')}
          </Button>
        </>
      )}
      {oidc && (
        <Button
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            startOidcSignIn(remember);
          }}
        >
          <LogIn className="mr-2 h-4 w-4" aria-hidden="true" />
          {t('auth.oidc.signIn', { name: oidc.label || t('auth.oidc.defaultLabel') })}
        </Button>
      )}
      {magicLink && (
        <Button
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy}
          onClick={() => {
            setLinkMode(true);
            setError(null);
          }}
        >
          <Mail className="mr-2 h-4 w-4" aria-hidden="true" />
          {t('auth.magicLink.button')}
        </Button>
      )}
    </form>
  );
}

/** Shown to an anonymous visitor once an account exists. */
export function LoginPage({
  onDone,
  session,
}: {
  onDone: (session: AuthSessionInfo) => void;
  /** The anonymous `GET /auth/session` answer, for the untrusted-proxy hint. */
  session?: AuthSessionInfo;
}) {
  const { t } = useTranslation();
  // Back from the OpenID Connect provider (read once, the address bar cleaned).
  const [oidcReturn] = useState(takeOidcReturn);
  // Opened from a sign-in link (read once, the fragment cleaned).
  const [magicLinkToken] = useState(takeMagicLink);
  return (
    <AuthLayout title={t('auth.login.title')} description={t('auth.login.description')}>
      {behindUntrustedHttpsProxy(session) && (
        <div
          role="note"
          className="flex gap-2 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>{t('auth.login.untrustedProxy')}</p>
        </div>
      )}
      {!window.isSecureContext && (
        <div
          role="note"
          className="flex gap-2 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            {t('auth.login.insecureContext')} {t('auth.passkey.insecureHint')}
          </p>
        </div>
      )}
      <LoginForm
        onDone={onDone}
        passkey={passkeyLoginAvailable(session)}
        oidc={session?.methods.oidc ? { label: session.methods.oidc_label } : undefined}
        initialChallenge={oidcReturn?.challenge ?? null}
        initialError={oidcReturn?.error ? t(`errors.${oidcReturn.error}`) : null}
        magicLink={session?.methods.magic_link === true}
        magicLinkToken={magicLinkToken}
      />
      {passkeyUnavailableHere(session) && (
        <p role="note" className="text-xs text-muted-foreground">
          {t('auth.passkey.unavailableHint')}
        </p>
      )}
    </AuthLayout>
  );
}
