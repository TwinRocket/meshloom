import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api, formatApiError } from '../api';
import { reportAuthRequired } from '../auth/authEvents';
import { passwordStrength } from '../auth/passwordStrength';
import { SessionProvider, useSession } from '../auth/SessionProvider';
import { KIOSK_SETUP_POLL_MS } from '../auth/SetupPage';
import type { AuthSessionInfo } from '../auth/types';
import i18n from '../i18n';

const base: AuthSessionInfo = {
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  auth_compat: { basic: false },
};
const admin = { id: 1, username: 'owner', display_name: 'Olive', role: 'admin' as const };
const signedIn: AuthSessionInfo = {
  ...base,
  authenticated: true,
  user: admin,
  via: 'lan',
  instance: { version: '5.0.0' },
};

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

/** Routes /api/auth calls to canned answers; anything else is a 404. */
function stubFetch(routes: Record<string, () => Response>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace('./api', '');
    const handler = routes[`${init?.method ?? 'GET'} ${url}`];
    return handler ? handler() : jsonResponse({ detail: 'nope' }, 404);
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function Probe() {
  const { user } = useSession();
  return <p data-testid="app">app for {user?.display_name}</p>;
}

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

beforeEach(async () => {
  await i18n.changeLanguage('fr');
});
afterEach(() => vi.unstubAllGlobals());

describe('session gate', () => {
  it('shows the setup page on a fresh instance, then loads the app without a reload', async () => {
    const user = userEvent.setup();
    const fetchFn = stubFetch({
      'GET /auth/session': () => jsonResponse({ ...base, setup_required: true }),
      'POST /auth/setup': () => jsonResponse(signedIn),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.setup.title') });
    expect(screen.queryByLabelText(t('auth.setup.code'))).toBeNull();
    expect(screen.queryByTestId('app')).toBeNull();

    await user.type(screen.getByLabelText(t('auth.username')), 'owner');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.type(
      screen.getByLabelText(t('auth.setup.confirmPassword')),
      'correct horse battery'
    );
    await user.click(screen.getByRole('button', { name: t('auth.setup.submit') }));

    expect(await screen.findByTestId('app')).toHaveTextContent('app for Olive');
    const post = fetchFn.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({
      username: 'owner',
      password: 'correct horse battery',
    });
  });

  it('asks for the installation code, with where to find it, on an existing database', async () => {
    const user = userEvent.setup();
    const fetchFn = stubFetch({
      'GET /auth/session': () =>
        jsonResponse({ ...base, setup_required: true, setup_code_required: true }),
      'POST /auth/setup': () =>
        jsonResponse({ detail: { code: 'auth_setup_code_invalid', message: 'bad' } }, 403),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    const code = await screen.findByLabelText(t('auth.setup.code'));
    expect(screen.getByText(/docker logs/)).toBeInTheDocument();
    expect(screen.getByText(/journalctl -u meshloom/)).toBeInTheDocument();
    expect(screen.getByText(/Journal du module/)).toBeInTheDocument();

    await user.type(code, '1234 5678');
    await user.type(screen.getByLabelText(t('auth.username')), 'owner');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.type(
      screen.getByLabelText(t('auth.setup.confirmPassword')),
      'correct horse battery'
    );
    await user.click(screen.getByRole('button', { name: t('auth.setup.submit') }));

    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_setup_code_invalid'));
    const post = fetchFn.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(String(post[1]!.body)).setup_code).toBe('1234 5678');
  });

  it('shows the kiosk banner with the installation code when the server sends it', async () => {
    stubFetch({
      'GET /auth/session': () =>
        jsonResponse({
          ...base,
          setup_required: true,
          setup_code_required: true,
          kiosk_setup_code: '12345678',
        }),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(await screen.findByText(t('auth.setup.kioskBanner.title'))).toBeInTheDocument();
    expect(screen.getByText('1234 5678')).toBeInTheDocument();
  });

  it('opens the kiosk session once the administrator was created elsewhere', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      const kioskUser = { id: 2, username: 'kiosk', display_name: 'Kiosk', role: 'kiosk' as const };
      let adminExists = false;
      stubFetch({
        'GET /auth/session': () =>
          adminExists
            ? jsonResponse({ ...signedIn, user: kioskUser })
            : jsonResponse({
                ...base,
                authenticated: true,
                user: kioskUser,
                setup_required: true,
                setup_code_required: true,
                kiosk_setup_code: '12345678',
              }),
      });
      render(
        <SessionProvider>
          <Probe />
        </SessionProvider>
      );
      expect(await screen.findByText(t('auth.setup.kioskBanner.title'))).toBeInTheDocument();
      await act(async () => {
        vi.advanceTimersByTime(KIOSK_SETUP_POLL_MS);
      });
      expect(screen.queryByTestId('app')).toBeNull();
      adminExists = true;
      await act(async () => {
        vi.advanceTimersByTime(KIOSK_SETUP_POLL_MS);
      });
      expect(await screen.findByTestId('app')).toHaveTextContent('app for Kiosk');
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows no kiosk banner without the code', async () => {
    stubFetch({
      'GET /auth/session': () =>
        jsonResponse({ ...base, setup_required: true, setup_code_required: true }),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByLabelText(t('auth.setup.code'));
    expect(screen.queryByText(t('auth.setup.kioskBanner.title'))).not.toBeInTheDocument();
  });

  it('keeps the setup button off until the password is long enough and confirmed', async () => {
    const user = userEvent.setup();
    stubFetch({ 'GET /auth/session': () => jsonResponse({ ...base, setup_required: true }) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByLabelText(t('auth.username'));
    await user.type(screen.getByLabelText(t('auth.username')), 'owner');
    await user.type(screen.getByLabelText(t('auth.password')), 'short');
    expect(screen.getByText(t('auth.setup.strength.tooShort'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: t('auth.setup.submit') })).toBeDisabled();
    await user.type(screen.getByLabelText(t('auth.password')), ' but now long enough');
    await user.type(screen.getByLabelText(t('auth.setup.confirmPassword')), 'different');
    expect(screen.getByText(t('auth.setup.passwordMismatch'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: t('auth.setup.submit') })).toBeDisabled();
  });

  it('shows the login page to an anonymous visitor and signs in', async () => {
    const user = userEvent.setup();
    stubFetch({
      'GET /auth/session': () => jsonResponse(base),
      'POST /auth/login': () => jsonResponse(signedIn),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.login.title') });
    await user.type(screen.getByLabelText(t('auth.username')), 'owner');
    await user.type(screen.getByLabelText(t('auth.password')), 'whatever');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    expect(await screen.findByTestId('app')).toBeInTheDocument();
  });

  it('warns on a page that is not a secure context', async () => {
    vi.stubGlobal('isSecureContext', false);
    stubFetch({ 'GET /auth/session': () => jsonResponse(base) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(await screen.findByRole('note')).toHaveTextContent(t('auth.login.insecureContext'));
  });

  it('warns on https when the server saw http (an untrusted proxy)', async () => {
    vi.stubGlobal('location', { ...window.location, protocol: 'https:' });
    stubFetch({ 'GET /auth/session': () => jsonResponse({ ...base, secure: false }) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(await screen.findByText(t('auth.login.untrustedProxy'))).toBeInTheDocument();
  });

  it('says nothing about the proxy when the server saw https', async () => {
    vi.stubGlobal('location', { ...window.location, protocol: 'https:' });
    stubFetch({ 'GET /auth/session': () => jsonResponse({ ...base, secure: true }) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.login.title') });
    expect(screen.queryByText(t('auth.login.untrustedProxy'))).toBeNull();
  });

  it('shows the wrong-password error and the rate-limit wait', async () => {
    const user = userEvent.setup();
    let answer = () =>
      jsonResponse({ detail: { code: 'auth_invalid_credentials', message: 'x' } }, 401);
    stubFetch({
      'GET /auth/session': () => jsonResponse(base),
      'POST /auth/login': () => answer(),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.login.title') });
    await user.type(screen.getByLabelText(t('auth.username')), 'owner');
    await user.type(screen.getByLabelText(t('auth.password')), 'nope');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('errors.auth_invalid_credentials')
    );
    // A failed login is a form error, not a lost session: the page stays the login page.
    expect(screen.getByRole('heading', { name: t('auth.login.title') })).toBeInTheDocument();

    answer = () =>
      jsonResponse({ detail: { code: 'auth_rate_limited', message: 'x' } }, 429, {
        'Retry-After': '42',
      });
    await user.type(screen.getByLabelText(t('auth.password')), 'nope');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        t('errors.auth_rate_limited', { retry_after: 42 })
      )
    );
  });

  it('shows no login wall when the credentials already authenticate (Basic compat)', async () => {
    stubFetch({
      'GET /auth/session': () => jsonResponse({ ...signedIn, auth_compat: { basic: true } }),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(await screen.findByTestId('app')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: t('auth.login.title') })).toBeNull();
  });

  it('offers a retry when the server cannot be reached', async () => {
    const user = userEvent.setup();
    let up = false;
    stubFetch({
      'GET /auth/session': () => {
        if (!up) throw new TypeError('Failed to fetch');
        return jsonResponse(signedIn);
      },
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByText(t('auth.sessionLoadFailed'));
    up = true;
    await user.click(screen.getByRole('button', { name: t('auth.retry') }));
    expect(await screen.findByTestId('app')).toBeInTheDocument();
  });

  it('keeps the app mounted under a re-login dialog when a session expires, and resumes', async () => {
    const user = userEvent.setup();
    const restored = vi.fn();
    window.addEventListener('meshloom:auth-restored', restored);
    stubFetch({
      'GET /auth/session': () => jsonResponse(signedIn),
      'POST /auth/login': () => jsonResponse(signedIn),
    });
    render(
      <SessionProvider>
        <Probe />
        <input aria-label="draft" />
      </SessionProvider>
    );
    await screen.findByTestId('app');
    await user.type(screen.getByLabelText('draft'), 'half a message');

    act(() => reportAuthRequired('auth_required'));
    const dialog = await screen.findByTestId('relogin-dialog');
    expect(dialog).toBeInTheDocument();
    // Still mounted, state intact.
    expect(screen.getByTestId('app')).toBeInTheDocument();
    expect(screen.getByLabelText('draft')).toHaveValue('half a message');

    await user.type(screen.getByLabelText(t('auth.password')), 'pw');
    await user.click(within(dialog).getByRole('button', { name: t('auth.login.submit') }));
    await waitFor(() => expect(screen.queryByTestId('relogin-dialog')).toBeNull());
    expect(screen.getByLabelText('draft')).toHaveValue('half a message');
    expect(restored).toHaveBeenCalled();
    window.removeEventListener('meshloom:auth-restored', restored);
  });

  it('goes back to the setup page when the server reports no account at all', async () => {
    let session: AuthSessionInfo = signedIn;
    stubFetch({ 'GET /auth/session': () => jsonResponse(session) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByTestId('app');
    session = { ...base, setup_required: true };
    act(() => reportAuthRequired('auth_setup_required'));
    await screen.findByRole('heading', { name: t('auth.setup.title') });
  });
});

describe('api.ts 401 and 429 handling', () => {
  it('reports a 401 on an app call to the gate with its code', async () => {
    const { onAuthRequired } = await import('../auth/authEvents');
    const seen = vi.fn();
    const off = onAuthRequired(seen);
    stubFetch({
      'GET /health': () =>
        jsonResponse({ detail: { code: 'auth_setup_required', message: 'x' } }, 401),
    });
    await expect(api.getHealth()).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledWith('auth_setup_required');
    off();
  });

  it('does not report a 401 from the login or setup forms', async () => {
    const { onAuthRequired } = await import('../auth/authEvents');
    const seen = vi.fn();
    const off = onAuthRequired(seen);
    stubFetch({
      'POST /auth/login': () =>
        jsonResponse({ detail: { code: 'auth_invalid_credentials', message: 'x' } }, 401),
    });
    await expect(api.authLogin({ username: 'a', password: 'b' })).rejects.toBeInstanceOf(ApiError);
    expect(seen).not.toHaveBeenCalled();
    off();
  });

  it('carries retry_after from the header or the params on a 429', async () => {
    stubFetch({
      'GET /health': () =>
        jsonResponse({ detail: { code: 'auth_rate_limited', message: 'x' } }, 429, {
          'Retry-After': '7',
        }),
    });
    const err = (await api.getHealth().catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfter).toBe(7);
    expect(formatApiError(err, i18n.t)).toContain(
      t('errors.auth_rate_limited', { retry_after: 7 })
    );

    const fromParams = new ApiError('x', 429, {
      code: 'auth_rate_limited',
      params: { retry_after: 12 },
    });
    expect(formatApiError(fromParams, i18n.t)).toContain(
      t('errors.auth_rate_limited', { retry_after: 12 })
    );
  });
});

describe('passwordStrength', () => {
  it('grades by length only', () => {
    expect(passwordStrength('')).toBe('empty');
    expect(passwordStrength('short')).toBe('tooShort');
    expect(passwordStrength('twelve chars')).toBe('ok');
    expect(passwordStrength('a'.repeat(16))).toBe('good');
    expect(passwordStrength('a'.repeat(24))).toBe('strong');
  });
});

describe('review fixes', () => {
  it('scrolls inside the page so the submit button is reachable on a small viewport', async () => {
    // jsdom does no layout: assert the classes that make the page scroll (body is overflow:hidden).
    stubFetch({
      'GET /auth/session': () =>
        jsonResponse({ ...base, setup_required: true, setup_code_required: true }),
    });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    const layout = await screen.findByTestId('auth-layout');
    expect(layout).toHaveClass('h-dvh', 'overflow-y-auto');
    expect(layout.firstElementChild).toHaveClass('m-auto');
    expect(layout).not.toHaveClass('min-h-dvh');
    expect(screen.getByRole('button', { name: t('auth.setup.submit') })).toBeInTheDocument();
  });

  const ingressAnonymous: AuthSessionInfo = {
    ...base,
    methods: { password: false, ingress: true },
  };
  const ingressSignedIn: AuthSessionInfo = {
    ...signedIn,
    methods: { password: false, ingress: true },
    via: 'ingress',
  };

  it('mounts the app straight away for a Home Assistant user through ingress', async () => {
    const fetchFn = stubFetch({ 'GET /auth/session': () => jsonResponse(ingressSignedIn) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(await screen.findByTestId('app')).toHaveTextContent('Olive');
    expect(screen.queryByLabelText(t('auth.password'))).toBeNull();
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('sends an anonymous ingress visit to Home Assistant, not to a password form', async () => {
    const user = userEvent.setup();
    let session = { ...ingressAnonymous, setup_required: true };
    const fetchFn = stubFetch({ 'GET /auth/session': () => jsonResponse(session) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.ingress.title') });
    expect(screen.queryByLabelText(t('auth.password'))).toBeNull();
    expect(screen.queryByRole('heading', { name: t('auth.setup.title') })).toBeNull();
    expect(screen.queryByTestId('app')).toBeNull();

    session = ingressSignedIn;
    await user.click(screen.getByRole('button', { name: t('auth.ingress.retry') }));
    await screen.findByTestId('app');
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('reloads the gate instead of asking a password when ingress loses the user', async () => {
    let session: AuthSessionInfo = ingressSignedIn;
    stubFetch({ 'GET /auth/session': () => jsonResponse(session) });
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    await screen.findByTestId('app');
    session = ingressAnonymous;
    act(() => reportAuthRequired('auth_required'));
    await screen.findByRole('heading', { name: t('auth.ingress.title') });
    expect(screen.queryByTestId('relogin-dialog')).toBeNull();
  });

  it('says so when Sign out cannot end a Basic session, and frees the button', async () => {
    const user = userEvent.setup();
    stubFetch({
      'GET /auth/session': () => jsonResponse(signedIn),
      'POST /auth/logout': () => jsonResponse({ ok: true }),
    });
    const { UserMenu } = await import('../auth/UserMenu');
    render(
      <SessionProvider>
        <UserMenu />
      </SessionProvider>
    );
    await user.click(await screen.findByRole('button', { name: /Olive/ }));
    await user.click(screen.getByRole('button', { name: t('auth.userMenu.signOut') }));
    expect(await screen.findByText(t('auth.userMenu.basicNote'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: t('auth.userMenu.signOut') })).toBeEnabled();
  });
});
