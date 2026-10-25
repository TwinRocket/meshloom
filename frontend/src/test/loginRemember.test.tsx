import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthSessionInfo } from '../auth/types';
import i18n from '../i18n';

const t = i18n.t.bind(i18n);

const anonymous: AuthSessionInfo = {
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false, passkey: false },
  auth_compat: { basic: false },
};
const signedIn: AuthSessionInfo = {
  ...anonymous,
  authenticated: true,
  user: { id: 1, username: 'root', display_name: 'Root', role: 'admin' },
  via: 'lan',
};

afterEach(() => vi.restoreAllMocks());

async function signIn(check: boolean) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(anonymous);
  const login = vi.spyOn(api, 'authLogin').mockResolvedValue(signedIn);
  const user = userEvent.setup();
  render(
    <SessionProvider>
      <div data-testid="app">app</div>
    </SessionProvider>
  );
  await user.type(await screen.findByLabelText(t('auth.username')), 'root');
  await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
  if (check) await user.click(screen.getByRole('checkbox', { name: t('auth.login.remember') }));
  await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
  await waitFor(() => expect(login).toHaveBeenCalled());
  return login;
}

describe('login: keep me signed in', () => {
  it('is unchecked by default and says what that means', async () => {
    const login = await signIn(false);
    expect(login).toHaveBeenCalledWith({
      username: 'root',
      password: 'correct horse battery',
      remember: false,
    });
    expect(screen.queryByText(t('auth.login.rememberHint'))).toBeNull(); // signed in: form gone
  });

  it('shows the hint next to the checkbox', async () => {
    vi.spyOn(api, 'getAuthSession').mockResolvedValue(anonymous);
    render(
      <SessionProvider>
        <div />
      </SessionProvider>
    );
    const box = await screen.findByRole('checkbox', { name: t('auth.login.remember') });
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription(t('auth.login.rememberHint'));
  });

  it('sends remember: true when ticked', async () => {
    const login = await signIn(true);
    expect(login).toHaveBeenCalledWith({
      username: 'root',
      password: 'correct horse battery',
      remember: true,
    });
  });
});
