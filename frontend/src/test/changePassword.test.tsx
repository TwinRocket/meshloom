import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import { reportPasswordChangeRequired } from '../auth/authEvents';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthSessionInfo } from '../auth/types';
import i18n from '../i18n';

const session = (mustChange: boolean): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  user: {
    id: 2,
    username: 'lea',
    display_name: 'Léa',
    role: 'member',
    must_change_password: mustChange,
  },
  via: 'lan',
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function stub(patch: () => Response) {
  let mustChange = true;
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace('./api', '');
    if (url === '/auth/session') return json(session(mustChange));
    if (url === '/auth/me' && init?.method === 'PATCH') {
      const res = patch();
      if (res.ok) mustChange = false;
      return res;
    }
    return json({ detail: 'nope' }, 404);
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterEach(() => vi.unstubAllGlobals());

/**
 * Lets the effects of the last commit run. The session lands from a promise, outside
 * `act`, so React schedules its passive effects (the gate's event listeners) after the
 * DOM already shows the app: an event reported right then reaches nobody.
 */
const settled = () => act(async () => {});

async function fill(user: ReturnType<typeof userEvent.setup>, current: string, next: string) {
  await user.type(await screen.findByLabelText(t('auth.changePassword.current')), current);
  await user.type(screen.getByLabelText(t('auth.changePassword.new')), next);
  await user.type(screen.getByLabelText(t('auth.setup.confirmPassword')), next);
  await user.click(screen.getByRole('button', { name: t('auth.changePassword.submit') }));
}

describe('forced password change', () => {
  it('replaces the app while must_change_password is set, then reloads the session', async () => {
    const user = userEvent.setup();
    const fetchFn = stub(() => json({ ok: true }));
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    await screen.findByRole('heading', { name: t('auth.changePassword.title') });
    expect(screen.queryByTestId('app')).toBeNull();

    await fill(user, 'temporary-secret', 'a much better secret');
    expect(await screen.findByTestId('app')).toBeInTheDocument();
    const patch = fetchFn.mock.calls.find(([, init]) => init?.method === 'PATCH')!;
    expect(JSON.parse(String(patch[1]!.body))).toEqual({
      current_password: 'temporary-secret',
      new_password: 'a much better secret',
    });
  });

  it('shows the password policy error and stays on the page', async () => {
    const user = userEvent.setup();
    stub(() =>
      json({ detail: { code: 'auth_password_policy', message: 'weak', params: {} } }, 400)
    );
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    await fill(user, 'temporary-secret', 'a much better secret');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(t('errors.auth_password_policy'));
    expect(alert).toHaveTextContent(/at least 12 characters/);
    expect(screen.queryByTestId('app')).toBeNull();
  });

  it('shows a wrong current password without leaving the page', async () => {
    const user = userEvent.setup();
    stub(() => json({ detail: { code: 'auth_invalid_credentials', message: 'no' } }, 403));
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    await fill(user, 'wrong', 'a much better secret');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('errors.auth_invalid_credentials')
    );
  });

  it('switches to the page when any call answers auth_password_change_required', async () => {
    const fetchFn = vi.fn(async () => json(session(false)));
    vi.stubGlobal('fetch', fetchFn);
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    await screen.findByTestId('app');
    await settled();
    act(() => reportPasswordChangeRequired());
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: t('auth.changePassword.title') })
      ).toBeInTheDocument()
    );
    // Over the app, not instead of it: its state survives the change.
    expect(screen.getByTestId('app')).toBeInTheDocument();
  });

  it('keeps the app mounted under the page after a mid-session refusal, with no loading screen', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input).replace('./api', '');
        if (url === '/auth/me' && init?.method === 'PATCH') return json({ ok: true });
        return json(session(false));
      })
    );
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    const app = await screen.findByTestId('app');
    await settled();
    act(() => reportPasswordChangeRequired());
    await fill(user, 'temporary-secret', 'a much better secret');
    await waitFor(() =>
      expect(screen.queryByLabelText(t('auth.changePassword.current'))).toBeNull()
    );
    expect(screen.queryByText(t('auth.loading'))).toBeNull();
    expect(screen.getByTestId('app')).toBe(app);
  });

  it('tells a kiosk to ask an administrator instead of showing the form', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({
          ...session(true),
          user: {
            id: 3,
            username: 'wall',
            display_name: 'Wall',
            role: 'kiosk',
            must_change_password: true,
          },
        })
      )
    );
    render(
      <SessionProvider>
        <p data-testid="app">app</p>
      </SessionProvider>
    );
    expect(await screen.findByText(t('auth.changePassword.kioskMessage'))).toBeInTheDocument();
    expect(screen.queryByLabelText(t('auth.changePassword.current'))).toBeNull();
  });
});
