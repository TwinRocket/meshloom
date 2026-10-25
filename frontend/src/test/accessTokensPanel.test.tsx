import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthRole, AuthSessionInfo, AuthTokenCreated, AuthTokenRow } from '../auth/types';
import { AccessTokensPanel, tokenScopesForRole } from '../components/settings/AccessTokensPanel';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const sessionOf = (role: AuthRole, via: 'lan' | 'ingress' = 'lan'): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  auth_compat: { basic: false },
  user: { id: 1, username: 'root', display_name: 'Root', role },
  via,
});

const row = (over: Partial<AuthTokenRow>): AuthTokenRow => ({
  id: 't1',
  name: 'ha-sensor',
  scopes: ['read'],
  created_at: 1_700_000_000,
  last_used_at: null,
  expires_at: null,
  origin: 'ui',
  user_id: 1,
  username: 'root',
  ...over,
});

const SECRET = 'mlt_secret-value-shown-once';

async function renderAs(role: AuthRole, via: 'lan' | 'ingress' = 'lan') {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(sessionOf(role, via));
  render(
    <SessionProvider>
      <AccessTokensPanel />
    </SessionProvider>
  );
  await screen.findByText(t('settings.access.tokens.title'));
}

describe('tokenScopesForRole', () => {
  it('offers the role’s permissions and nothing for the others', () => {
    expect(tokenScopesForRole('member')).toEqual(['read', 'read_private', 'send']);
    expect(tokenScopesForRole('admin')).toEqual([
      'read',
      'read_private',
      'send',
      'repeater_admin',
      'admin',
    ]);
    expect(tokenScopesForRole('viewer')).toEqual(['read', 'read_private']);
    expect(tokenScopesForRole(null)).toEqual([]);
  });
});

describe('AccessTokensPanel', () => {
  let rows: AuthTokenRow[];
  beforeEach(() => {
    rows = [row({})];
    vi.spyOn(api, 'listAuthTokens').mockImplementation(async () => rows);
  });
  afterEach(() => vi.restoreAllMocks());

  it('lists a member’s tokens without the owner', async () => {
    rows = [row({ last_used_at: 1_700_000_500, expires_at: 4_000_000_000 })];
    await renderAs('member');
    const name = await screen.findByText('ha-sensor');
    const item = name.closest('li')!;
    expect(within(item).getByText('read')).toBeInTheDocument();
    expect(item.textContent).not.toContain(t('settings.access.tokens.owner', { user: 'root' }));
    expect(item.textContent).toContain('·');
  });

  it('shows the owner to an administrator', async () => {
    rows = [row({ user_id: 2, username: 'bob' })];
    await renderAs('admin');
    const item = (await screen.findByText('ha-sensor')).closest('li')!;
    expect(item.textContent).toContain(t('settings.access.tokens.owner', { user: 'bob' }));
  });

  it('renders nothing for a viewer and never calls the API', async () => {
    vi.spyOn(api, 'getAuthSession').mockResolvedValue(sessionOf('viewer'));
    render(
      <SessionProvider>
        <AccessTokensPanel />
      </SessionProvider>
    );
    await waitFor(() => expect(api.getAuthSession).toHaveBeenCalled());
    expect(screen.queryByText(t('settings.access.tokens.title'))).toBeNull();
    expect(api.listAuthTokens).not.toHaveBeenCalled();
  });

  it('explains that tokens do not work through Home Assistant ingress', async () => {
    await renderAs('member', 'ingress');
    expect(screen.getByText(t('settings.access.tokens.ingressNotice'))).toBeInTheDocument();
  });

  it('offers only the scopes of a member and sends the chosen ones with an expiry', async () => {
    const created: AuthTokenCreated = { ...row({ id: 't2', name: 'phone' }), token: SECRET };
    const create = vi.spyOn(api, 'createAuthToken').mockResolvedValue(created);
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    expect(screen.queryByLabelText('repeater_admin')).toBeNull();
    expect(screen.queryByLabelText(/^admin/)).toBeNull();
    const submit = screen.getByRole('button', { name: t('settings.access.tokens.create') });
    expect(submit).toBeDisabled();
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), '  phone ');
    await ui.click(screen.getByRole('checkbox', { name: /read_private/ }));
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    // fireEvent: Radix leaves `pointer-events: none` on the body while the dialog is open.
    fireEvent.change(screen.getByLabelText(t('settings.access.tokens.expiry')), {
      target: { value: '30' },
    });
    const before = Math.floor(Date.now() / 1000);
    await ui.click(submit);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const body = create.mock.calls[0][0];
    expect(body.name).toBe('phone');
    expect(body.scopes).toEqual(['read', 'read_private']);
    expect(body.expires_at).toBeGreaterThanOrEqual(before + 30 * 86400);
    expect(body.expires_at).toBeLessThanOrEqual(before + 30 * 86400 + 5);
  });

  it('shows the secret once with a warning, then never again', async () => {
    const created: AuthTokenCreated = { ...row({ id: 't2', name: 'phone' }), token: SECRET };
    vi.spyOn(api, 'createAuthToken').mockResolvedValue(created);
    await renderAs('admin');
    const ui = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), 'phone');
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.create') }));

    const secret = (await screen.findByLabelText(
      t('settings.access.tokens.secretLabel')
    )) as HTMLInputElement;
    expect(secret.value).toBe(SECRET);
    expect(screen.getByText(t('settings.access.tokens.secretWarning'))).toBeInTheDocument();
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.copy') }));
    expect(writeText).toHaveBeenCalledWith(SECRET);

    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.secretDone') }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.textContent).not.toContain(SECRET);
    // The refreshed list never carries it either.
    expect(api.listAuthTokens).toHaveBeenCalledTimes(2);
  });

  it('never logs the secret, nor puts it in storage or the address', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((method) =>
      vi.spyOn(console, method).mockImplementation(() => undefined)
    );
    vi.spyOn(api, 'createAuthToken').mockResolvedValue({ ...row({}), token: SECRET });
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), 'x');
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.create') }));
    await screen.findByLabelText(t('settings.access.tokens.secretLabel'));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.secretDone') }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    for (const spy of spies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(SECRET);
    }
    expect(JSON.stringify({ ...localStorage })).not.toContain(SECRET);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(SECRET);
    expect(window.location.href).not.toContain(SECRET);
  });

  it('lets the secret field shrink next to the Copy button (360 px)', async () => {
    // jsdom has no layout: assert the classes that keep the row inside a 360 px dialog.
    // `min-w-0` lets the flex item go below its content width; without it a long
    // `mlt_...` value pushes the row wider than the dialog.
    vi.spyOn(api, 'createAuthToken').mockResolvedValue({
      ...row({}),
      token: `mlt_${'x'.repeat(80)}`,
    });
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), 'x');
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.create') }));
    const secret = await screen.findByLabelText(t('settings.access.tokens.secretLabel'));
    expect(secret).toHaveClass('min-w-0', 'flex-1');
  });

  it('keeps the secret on screen when the reader clicks outside', async () => {
    vi.spyOn(api, 'createAuthToken').mockResolvedValue({ ...row({}), token: SECRET });
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), 'x');
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.create') }));
    await screen.findByLabelText(t('settings.access.tokens.secretLabel'));
    await ui.keyboard('{Escape}');
    expect(screen.getByLabelText(t('settings.access.tokens.secretLabel'))).toBeInTheDocument();
  });

  it('shows a coded refusal in the dialog', async () => {
    vi.spyOn(api, 'createAuthToken').mockRejectedValue(
      new ApiError('Conflict', 409, {
        code: 'auth_token_limit',
        params: { max: 100 },
      })
    );
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(await screen.findByRole('button', { name: t('settings.access.tokens.add') }));
    await ui.type(screen.getByLabelText(t('settings.access.tokens.name')), 'x');
    await ui.click(screen.getByRole('checkbox', { name: /^read\b(?!_)/ }));
    await ui.click(screen.getByRole('button', { name: t('settings.access.tokens.create') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_token_limit'));
  });

  it('revokes only after a confirmation', async () => {
    const revoke = vi.spyOn(api, 'revokeAuthToken').mockResolvedValue(undefined);
    await renderAs('member');
    const ui = userEvent.setup();
    await ui.click(
      await screen.findByRole('button', {
        name: t('settings.access.tokens.revokeNamed', { name: 'ha-sensor' }),
      })
    );
    expect(revoke).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: t('settings.access.cancel') }));
    expect(revoke).not.toHaveBeenCalled();
    await ui.click(
      await screen.findByRole('button', {
        name: t('settings.access.tokens.revokeNamed', { name: 'ha-sensor' }),
      })
    );
    const confirm = await screen.findByRole('dialog');
    await ui.click(
      within(confirm).getByRole('button', { name: t('settings.access.tokens.revoke') })
    );
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('t1'));
  });
});
