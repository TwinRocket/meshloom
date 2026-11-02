import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { LoginPage } from '../auth/LoginPage';
import * as oidc from '../auth/oidc';
import type { AuthSessionInfo, AuthUserRow, OidcSettings } from '../auth/types';
import { AccessOidcPanel } from '../components/settings/AccessOidcPanel';
import { SettingsConnectionSection } from '../components/settings/SettingsConnectionSection';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

vi.mock('../auth/oidc', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/oidc')>();
  return { ...actual, startOidcSignIn: vi.fn() };
});

const t = i18n.t.bind(i18n);

const anonymous = (methods: Partial<AuthSessionInfo['methods']> = {}): AuthSessionInfo => ({
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false, passkey: false, ...methods },
});

const settings = (over: Partial<OidcSettings> = {}): OidcSettings => ({
  enabled: false,
  preset: 'generic',
  label: '',
  issuer: '',
  client_id: '',
  client_secret_set: false,
  scopes: 'openid email profile',
  token_auth_method: 'client_secret_basic',
  auto_create: false,
  allow_private_network: false,
  updated_at: 0,
  redirect_uris: ['https://mesh.example/api/auth/oidc/callback'],
  secret_store_available: true,
  ...over,
});

const user = (over: Partial<AuthUserRow>): AuthUserRow => ({
  id: 2,
  username: 'bob',
  display_name: '',
  role: 'member',
  provider: 'local',
  disabled: false,
  must_change_password: false,
  has_password: true,
  totp_enabled: false,
  created_at: 1,
  updated_at: 1,
  ...over,
});

beforeEach(() => {
  oidc.resetOidcReturn();
  window.history.replaceState(null, '', '/');
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(oidc.startOidcSignIn).mockClear();
});

describe('the OpenID Connect return', () => {
  it('parses an error, a pending second factor, or nothing', () => {
    expect(oidc.parseOidcReturn('?oidc_error=auth_oidc_no_account', '')).toEqual({
      error: 'auth_oidc_no_account',
    });
    expect(oidc.parseOidcReturn('?oidc_error=<script>', '')).toEqual({ error: 'auth_oidc_failed' });
    expect(
      oidc.parseOidcReturn('', '#oidc_2fa=tok&methods=totp%2Crecovery%2Cbogus&expires_in=120')
    ).toEqual({
      challenge: { pending_token: 'tok', methods: ['totp', 'recovery'], expires_in: 120 },
    });
    expect(oidc.parseOidcReturn('', '#settings/access')).toBeNull();
    expect(oidc.oidcStartUrl(true)).toBe(
      `./api/auth/oidc/start?remember=true&base=${encodeURIComponent(oidc.appBase())}`
    );
  });

  it('is read once and removed from the address bar', () => {
    window.history.replaceState(null, '', '/?oidc_error=auth_oidc_denied&keep=1');
    expect(oidc.takeOidcReturn()).toEqual({ error: 'auth_oidc_denied' });
    expect(window.location.search).toBe('?keep=1');
    window.history.replaceState(null, '', '/#oidc_2fa=tok');
    // Same page load: the first answer stays, the hash is not read again.
    expect(oidc.takeOidcReturn()).toEqual({ error: 'auth_oidc_denied' });
  });
});

describe('LoginPage with OpenID Connect', () => {
  it('offers the provider only when the server says so, and keeps "remember me"', async () => {
    const { unmount } = render(<LoginPage onDone={vi.fn()} session={anonymous()} />);
    expect(screen.queryByRole('button', { name: /Keycloak/ })).toBeNull();
    unmount();
    render(
      <LoginPage onDone={vi.fn()} session={anonymous({ oidc: true, oidc_label: 'Keycloak' })} />
    );
    const ui = userEvent.setup();
    await ui.click(screen.getByLabelText(t('auth.login.remember')));
    await ui.click(
      screen.getByRole('button', { name: t('auth.oidc.signIn', { name: 'Keycloak' }) })
    );
    expect(oidc.startOidcSignIn).toHaveBeenCalledWith(true);
  });

  it('names a provider without a label generically', () => {
    render(<LoginPage onDone={vi.fn()} session={anonymous({ oidc: true })} />);
    expect(
      screen.getByRole('button', {
        name: t('auth.oidc.signIn', { name: t('auth.oidc.defaultLabel') }),
      })
    ).toBeInTheDocument();
  });

  it('shows a refusal brought back from the provider', () => {
    window.history.replaceState(null, '', '/?oidc_error=auth_oidc_no_account');
    render(<LoginPage onDone={vi.fn()} session={anonymous({ oidc: true })} />);
    expect(screen.getByRole('alert')).toHaveTextContent(t('errors.auth_oidc_no_account'));
    expect(window.location.search).toBe('');
  });

  it('asks for the TOTP code an account owes after the provider', async () => {
    window.history.replaceState(
      null,
      '',
      '/#oidc_2fa=pending-1&methods=totp,recovery&expires_in=300'
    );
    const session: AuthSessionInfo = {
      ...anonymous(),
      authenticated: true,
      user: { id: 2, username: 'alice', display_name: '', role: 'member' },
    };
    const login2fa = vi.spyOn(api, 'authLogin2fa').mockResolvedValue(session);
    const onDone = vi.fn();
    render(<LoginPage onDone={onDone} session={anonymous({ oidc: true })} />);
    expect(window.location.hash).toBe('');
    const input = screen.getByLabelText(t('auth.secondFactor.codeLabel'));
    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(session));
    expect(login2fa.mock.calls[0][0]).toMatchObject({ pending_token: 'pending-1', code: '123456' });
  });
});

describe('Settings → Connection → OpenID Connect', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue({
      trusted_proxies: [],
      audit_retention_days: 366,
      ingress_default_role: 'member',
      session_lifetime_days: 30,
    });
    vi.spyOn(api, 'listOidcLinks').mockResolvedValue({ issuer: '', links: [] });
    vi.spyOn(api, 'listAuthUsers').mockResolvedValue([
      user({ id: 1, username: 'root', role: 'admin' }),
      user({ id: 2, username: 'bob' }),
      user({ id: 3, username: 'screen', role: 'kiosk', has_password: false }),
      user({ id: 4, username: 'ha', provider: 'ingress', has_password: false }),
    ]);
  });

  it('is a tab of the Connection section', async () => {
    vi.spyOn(api, 'getOidcSettings').mockResolvedValue(settings());
    render(<SettingsConnectionSection />);
    for (const tab of ['local', 'lan', 'bans', 'oidc']) {
      expect(screen.getByTestId(`connection-tab-${tab}`)).toHaveTextContent(
        t(`settings.connection.tabs.${tab}`)
      );
    }
    await userEvent.setup().click(screen.getByTestId('connection-tab-oidc'));
    expect(await screen.findByLabelText(t('settings.access.oidc.issuer'))).toBeInTheDocument();
  });

  it('fills a preset and saves, sending the secret only when typed', async () => {
    vi.spyOn(api, 'getOidcSettings').mockResolvedValue(settings());
    const patch = vi
      .spyOn(api, 'patchOidcSettings')
      .mockImplementation(async (body) =>
        settings({ ...body, client_secret_set: true } as Partial<OidcSettings>)
      );
    render(<AccessOidcPanel />);
    const preset = await screen.findByLabelText(t('settings.access.oidc.preset'));
    fireEvent.change(preset, { target: { value: 'google' } });
    expect(screen.getByLabelText(t('settings.access.oidc.issuer'))).toHaveValue(
      'https://accounts.google.com'
    );
    expect(screen.getByLabelText(t('settings.access.oidc.label'))).toHaveValue('Google');
    fireEvent.change(screen.getByLabelText(t('settings.access.oidc.client_id')), {
      target: { value: 'client-1' },
    });
    fireEvent.change(screen.getByLabelText(t('settings.access.oidc.clientSecret')), {
      target: { value: 'top-secret' },
    });
    fireEvent.click(screen.getByLabelText(t('settings.access.oidc.enabled')));
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.oidc.save') }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toMatchObject({
      enabled: true,
      preset: 'google',
      issuer: 'https://accounts.google.com',
      client_id: 'client-1',
      client_secret: 'top-secret',
    });
    // The secret field empties itself; the stored secret is never shown.
    await waitFor(() =>
      expect(screen.getByLabelText(t('settings.access.oidc.clientSecret'))).toHaveValue('')
    );
    expect(screen.getByLabelText(t('settings.access.oidc.clientSecret'))).toHaveAttribute(
      'placeholder',
      t('settings.access.oidc.secretKept')
    );
    expect(screen.getByTestId('oidc-redirect-uris')).toHaveTextContent(
      'https://mesh.example/api/auth/oidc/callback'
    );
  });

  it('tests the saved provider and tells the announced issuer on a mismatch', async () => {
    vi.spyOn(api, 'getOidcSettings').mockResolvedValue(
      settings({ issuer: 'https://idp.example/', client_id: 'c' })
    );
    const { toast } = await import('../components/ui/sonner');
    const testOidc = vi.spyOn(api, 'testOidc').mockRejectedValueOnce(
      new ApiError('Bad Request', 400, {
        code: 'auth_oidc_issuer_mismatch',
        message: 'x',
        params: { issuer: 'https://idp.example' },
      })
    );
    render(<AccessOidcPanel />);
    const button = await screen.findByRole('button', { name: t('settings.access.oidc.test') });
    fireEvent.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const calls = vi.mocked(toast.error).mock.calls;
    expect(String(calls[calls.length - 1][0])).toContain('https://idp.example');
    testOidc.mockResolvedValueOnce({
      ok: true,
      issuer: 'https://idp.example/',
      authorization_endpoint: 'a',
      token_endpoint: 'b',
      keys: 2,
    });
    fireEvent.click(button);
    expect(await screen.findByTestId('oidc-test-ok')).toHaveTextContent(
      t('settings.access.oidc.testOk', { issuer: 'https://idp.example/', keys: 2 })
    );
  });

  it('links an identity to a local account, never a kiosk or Home Assistant one', async () => {
    vi.spyOn(api, 'getOidcSettings').mockResolvedValue(
      settings({ issuer: 'https://idp.example', client_id: 'c' })
    );
    vi.spyOn(api, 'listOidcLinks').mockResolvedValue({ issuer: 'https://idp.example', links: [] });
    const add = vi.spyOn(api, 'addOidcLink').mockResolvedValue({
      id: 1,
      user_id: 2,
      username: 'bob',
      issuer: 'https://idp.example',
      subject: null,
      email: 'bob@example.org',
      created_at: 1,
      last_login_at: null,
    });
    render(<AccessOidcPanel />);
    const account = await screen.findByLabelText(t('settings.access.oidc.links.account'));
    const names = within(account)
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(names).toEqual(['root', 'bob']);
    fireEvent.change(account, { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText(t('settings.access.oidc.links.email')), {
      target: { value: 'bob@example.org' },
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.access.oidc.links.add') }));
    await waitFor(() => expect(add).toHaveBeenCalledWith({ user_id: 2, email: 'bob@example.org' }));
  });
});
