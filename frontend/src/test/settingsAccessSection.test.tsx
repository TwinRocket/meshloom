import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { formatAccessError } from '../auth/accessErrors';
import { SessionProvider } from '../auth/SessionProvider';
import type {
  AuthMe,
  AuthRole,
  AuthSessionInfo,
  AuthSessionRow,
  AuthSettings,
  AuthUserRow,
} from '../auth/types';
import { SettingsAccessSection } from '../components/settings/SettingsAccessSection';
import { deviceLabel } from '../components/settings/accessShared';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

// The test language is the app default (French): assertions go through the same i18n.
const t = i18n.t.bind(i18n);

const sessionOf = (role: AuthRole): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  auth_compat: { basic: false },
  user: { id: 1, username: 'root', display_name: 'Root', role },
  via: 'lan',
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

let users: AuthUserRow[];
let sessions: AuthSessionRow[];
let settings: AuthSettings;

function mockApi() {
  const me: AuthMe = {
    id: 1,
    username: 'root',
    display_name: 'Root',
    role: 'admin',
    provider: 'local',
    has_password: true,
    must_change_password: false,
    created_at: 1,
  };
  vi.spyOn(api, 'getAuthMe').mockImplementation(async () => me);
  vi.spyOn(api, 'listAuthSessions').mockImplementation(async () => sessions);
  vi.spyOn(api, 'listAuthUsers').mockImplementation(async () => users);
  vi.spyOn(api, 'getAuthSettings').mockImplementation(async () => settings);
}

async function renderAs(role: AuthRole) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(sessionOf(role));
  render(
    <SessionProvider>
      <SettingsAccessSection />
    </SessionProvider>
  );
  await screen.findByText(t('settings.access.profile.title'));
}

describe('SettingsAccessSection', () => {
  beforeEach(() => {
    users = [
      user({ id: 1, username: 'root', display_name: 'Root', role: 'admin' }),
      user({ id: 2, username: 'bob' }),
      user({ id: 3, username: 'ha-user', provider: 'ingress', has_password: false }),
    ];
    sessions = [
      {
        id: 's1',
        created_at: 1,
        last_seen_at: 1_700_000_000,
        expires_at: 2,
        ip: '10.0.0.2',
        user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36',
        via: 'lan',
        current: true,
      },
      {
        id: 's2',
        created_at: 1,
        last_seen_at: 1_700_000_000,
        expires_at: 2,
        ip: '10.0.0.9',
        user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1',
        via: 'trusted',
        current: false,
      },
    ];
    settings = {
      trusted_proxies: [],
      audit_retention_days: 366,
      ingress_default_role: 'member',
    };
    mockApi();
  });
  afterEach(() => vi.restoreAllMocks());

  it('shows the administrator parts only to an administrator', async () => {
    await renderAs('admin');
    expect(await screen.findByText(t('settings.access.users.title'))).toBeInTheDocument();
    expect(screen.getByText(t('settings.access.ingress.title'))).toBeInTheDocument();
  });

  it.each(['member', 'viewer'] as const)('hides them from a %s', async (role) => {
    await renderAs(role);
    expect(await screen.findByText(t('settings.access.sessions.title'))).toBeInTheDocument();
    expect(screen.queryByText(t('settings.access.users.title'))).toBeNull();
    expect(screen.queryByText(t('settings.access.ingress.title'))).toBeNull();
    expect(api.listAuthUsers).not.toHaveBeenCalled();
    expect(api.getAuthSettings).not.toHaveBeenCalled();
  });

  it('lists users with provider badges and locks the admin out of changing themselves', async () => {
    await renderAs('admin');
    const root = await screen.findByTestId('access-user-root');
    expect(within(root).getByText(t('settings.access.users.you'))).toBeInTheDocument();
    expect(within(root).getByRole('combobox')).toBeDisabled();
    const ha = screen.getByTestId('access-user-ha-user');
    expect(within(ha).getByText('Home Assistant')).toBeInTheDocument();
    expect(within(ha).queryByRole('button', { name: t('settings.access.users.reset') })).toBeNull();
  });

  it('creates a member, a viewer and a kiosk', async () => {
    const create = vi
      .spyOn(api, 'createAuthUser')
      .mockImplementation(async (body) => user({ username: body.username, role: body.role }));
    await renderAs('admin');
    const ui = userEvent.setup();
    const cases: [AuthRole, boolean][] = [
      ['member', true],
      ['viewer', true],
      ['kiosk', false],
    ];
    for (const [role, hasPassword] of cases) {
      await ui.click(await screen.findByRole('button', { name: t('settings.access.users.add') }));
      await ui.type(screen.getByLabelText(t('auth.username')), `new-${role}`);
      // fireEvent: Radix leaves `pointer-events: none` on the body while the dialog is open.
      fireEvent.change(screen.getByLabelText(t('settings.access.users.role')), {
        target: { value: role },
      });
      if (hasPassword) {
        await ui.type(
          screen.getByLabelText(t('settings.access.users.password')),
          'correct horse battery'
        );
      } else {
        expect(screen.queryByLabelText(t('settings.access.users.password'))).toBeNull();
      }
      await ui.click(screen.getByRole('button', { name: t('settings.access.users.create') }));
      await waitFor(() =>
        expect(create).toHaveBeenCalledTimes(cases.findIndex((c) => c[0] === role) + 1)
      );
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    }
    expect(create.mock.calls[0][0]).toMatchObject({
      username: 'new-member',
      role: 'member',
      password: 'correct horse battery',
      must_change_password: true,
    });
    expect(create.mock.calls[2][0]).toEqual({
      username: 'new-kiosk',
      role: 'kiosk',
      display_name: '',
    });
  });

  it('changes a role and disables an account', async () => {
    const patch = vi.spyOn(api, 'patchAuthUser').mockImplementation(async (id) => user({ id }));
    await renderAs('admin');
    const ui = userEvent.setup();
    const bob = await screen.findByTestId('access-user-bob');
    await ui.selectOptions(within(bob).getByRole('combobox'), 'viewer');
    await waitFor(() => expect(patch).toHaveBeenCalledWith(2, { role: 'viewer' }));
    await ui.click(within(bob).getByRole('button', { name: t('settings.access.users.disable') }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(2, { disabled: true }));
  });

  it('resets a password asking for a change at next sign-in, and revokes sessions', async () => {
    const reset = vi.spyOn(api, 'setAuthUserPassword').mockImplementation(async () => user({}));
    const revoke = vi.spyOn(api, 'revokeAuthUserSessions').mockResolvedValue({ revoked: 2 });
    await renderAs('admin');
    const ui = userEvent.setup();
    const bob = await screen.findByTestId('access-user-bob');
    await ui.click(within(bob).getByRole('button', { name: t('settings.access.users.reset') }));
    await ui.type(
      screen.getByLabelText(t('settings.access.users.password')),
      'another long passphrase'
    );
    await ui.click(screen.getByRole('button', { name: t('settings.access.users.resetSubmit') }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith(2, 'another long passphrase', true));
    await ui.click(
      within(bob).getByRole('button', { name: t('settings.access.users.revokeSessions') })
    );
    await waitFor(() => expect(revoke).toHaveBeenCalledWith(2));
  });

  it('shows the coded last-administrator error in the dialog language', async () => {
    const err = new ApiError('x', 409, { code: 'auth_last_admin' });
    expect(formatAccessError(err, t)).toBe(t('errors.auth_last_admin'));
    expect(formatAccessError(err, i18n.getFixedT('fr'))).toBe(
      i18n.getFixedT('fr')('errors.auth_last_admin')
    );
  });

  it('explains password policy, 429 and 503 errors', () => {
    const policy = new ApiError('x', 400, {
      code: 'auth_password_policy',
      params: { reason: 'common', min_length: 12 },
    });
    expect(formatAccessError(policy, t)).toContain('12');
    const limited = new ApiError('x', 429, { code: 'auth_rate_limited' }, 30);
    expect(formatAccessError(limited, t)).toContain('30');
    expect(formatAccessError(new ApiError('x', 429, 'slow down'), t)).toBe(
      t('errors.access_rate_limited', { retry_after: 30 })
    );
    expect(formatAccessError(new ApiError('x', 503, 'busy'), t)).toBe(
      t('errors.access_unavailable')
    );
    expect(formatAccessError(new ApiError('x', 503, { code: 'auth_busy' }), t)).toBe(
      t('errors.auth_busy')
    );
  });

  it('revokes one of the own sessions and refreshes the list', async () => {
    const revoke = vi.spyOn(api, 'revokeAuthSession').mockImplementation(async (id) => {
      sessions = sessions.filter((row) => row.id !== id);
      return { ok: true };
    });
    await renderAs('member');
    const ui = userEvent.setup();
    expect(await screen.findByText('Safari · iOS')).toBeInTheDocument();
    expect(screen.getByText('Chrome · macOS')).toBeInTheDocument();
    await ui.click(screen.getByRole('button', { name: t('settings.access.sessions.revoke') }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('s2'));
    await waitFor(() => expect(screen.queryByText('Safari · iOS')).toBeNull());
    expect(
      screen.queryByRole('button', { name: t('settings.access.sessions.signOutOthers') })
    ).toBeNull();
  });

  it('signs out everywhere else', async () => {
    const others = vi.spyOn(api, 'revokeOtherAuthSessions').mockResolvedValue({ revoked: 1 });
    await renderAs('viewer');
    await userEvent
      .setup()
      .click(
        await screen.findByRole('button', { name: t('settings.access.sessions.signOutOthers') })
      );
    await waitFor(() => expect(others).toHaveBeenCalled());
  });

  it('changes the own password, and saves the Home Assistant default role', async () => {
    const change = vi.spyOn(api, 'changeOwnPassword').mockResolvedValue({});
    const patchSettings = vi
      .spyOn(api, 'patchAuthSettings')
      .mockResolvedValue({ ...settings, ingress_default_role: 'viewer' });
    await renderAs('admin');
    const ui = userEvent.setup();
    await ui.type(screen.getByLabelText(t('settings.access.profile.currentPassword')), 'old-pass');
    await ui.type(
      screen.getByLabelText(t('settings.access.profile.newPassword')),
      'a brand new passphrase'
    );
    await ui.type(
      screen.getByLabelText(t('settings.access.profile.confirmPassword')),
      'a brand new passphrase'
    );
    await ui.click(
      screen.getByRole('button', { name: t('settings.access.profile.changePassword') })
    );
    await waitFor(() => expect(change).toHaveBeenCalledWith('old-pass', 'a brand new passphrase'));
    await ui.selectOptions(
      await screen.findByLabelText(t('settings.access.ingress.defaultRole')),
      'viewer'
    );
    await waitFor(() =>
      expect(patchSettings).toHaveBeenCalledWith({ ingress_default_role: 'viewer' })
    );
    expect(
      screen.getByRole('link', { name: t('settings.access.ingress.proxiesLink') })
    ).toHaveAttribute('href', '#settings/local');
  });
});

describe('deviceLabel', () => {
  it('names the browser and system, never the raw user agent', () => {
    expect(deviceLabel(null, '?')).toBe('?');
    expect(deviceLabel('Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0', '?')).toBe(
      'Firefox · Linux'
    );
    expect(deviceLabel('curl/8', '?')).toBe('?');
  });
});
