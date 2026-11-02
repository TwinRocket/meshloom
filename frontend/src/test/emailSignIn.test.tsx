import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { LoginPage } from '../auth/LoginPage';
import * as magicLink from '../auth/magicLink';
import { SecondFactorStep } from '../auth/SecondFactorStep';
import type { AuthSessionInfo, EmailFactorStatus } from '../auth/types';
import { AccessEmailFactorPanel } from '../components/settings/AccessEmailFactorPanel';
import { AccessEmailSignInPanel } from '../components/settings/AccessEmailSignInPanel';
import { CONNECTION_TABS } from '../components/settings/SettingsConnectionSection';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const anonymous = (methods: Partial<AuthSessionInfo['methods']> = {}): AuthSessionInfo => ({
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false, passkey: false, ...methods },
});

const signedIn: AuthSessionInfo = {
  ...anonymous(),
  authenticated: true,
  user: { id: 2, username: 'alice', display_name: '', role: 'member' },
};

const TOKEN = 'A'.repeat(43);

beforeEach(() => {
  magicLink.resetMagicLink();
  window.history.replaceState(null, '', '/');
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('sign-in links', () => {
  it('reads the token from the fragment only', () => {
    expect(magicLink.parseMagicLink(`#magic_link=${TOKEN}`)).toBe(TOKEN);
    expect(magicLink.parseMagicLink('#magic_link=<script>')).toBeNull();
    expect(magicLink.parseMagicLink('#oidc_2fa=x')).toBeNull();
  });

  it('trades the token of the address bar for a session and cleans it', async () => {
    window.history.replaceState(null, '', `/#magic_link=${TOKEN}`);
    const verify = vi.spyOn(api, 'verifyMagicLink').mockResolvedValue(signedIn);
    const onDone = vi.fn();
    render(<LoginPage onDone={onDone} session={anonymous()} />);
    expect(window.location.hash).toBe('');
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(signedIn));
    expect(verify).toHaveBeenCalledWith(TOKEN);
  });

  it('asks for a link by username and says the same thing whatever the answer', async () => {
    const request = vi
      .spyOn(api, 'requestMagicLink')
      .mockResolvedValue({ sent: true, expires_in: 900 });
    render(<LoginPage onDone={vi.fn()} session={anonymous({ magic_link: true })} />);
    const ui = userEvent.setup();
    await ui.click(screen.getByRole('button', { name: t('auth.magicLink.button') }));
    await ui.type(screen.getByLabelText(t('auth.username')), 'alice');
    await ui.click(screen.getByRole('button', { name: t('auth.magicLink.send') }));
    expect(await screen.findByRole('status')).toHaveTextContent(t('auth.magicLink.sent'));
    expect(request.mock.calls[0][0]).toMatchObject({ username: 'alice', remember: false });
  });

  it('offers no link without the server saying so', () => {
    render(<LoginPage onDone={vi.fn()} session={anonymous()} />);
    expect(screen.queryByRole('button', { name: t('auth.magicLink.button') })).toBeNull();
  });
});

describe('the e-mail code step', () => {
  it('sends the code at once when it is the only factor and answers with method email', async () => {
    const send = vi.spyOn(api, 'sendLoginEmailCode').mockResolvedValue({
      sent: true,
      expires_in: 600,
      resend_in: 60,
      email_hint: 'a***@example.org',
    });
    const login2fa = vi.spyOn(api, 'authLogin2fa').mockResolvedValue(signedIn);
    const onDone = vi.fn();
    render(
      <SecondFactorStep
        challenge={{ pending_token: 'p-1', methods: ['email'], expires_in: 300 }}
        onDone={onDone}
        onRestart={vi.fn()}
      />
    );
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(
      await screen.findByText(t('auth.emailCode.sentTo', { address: 'a***@example.org' }))
    ).toBeInTheDocument();
    const input = screen.getByLabelText(t('auth.emailCode.codeLabel'));
    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(signedIn));
    expect(login2fa.mock.calls[0][0]).toEqual({
      pending_token: 'p-1',
      code: '123456',
      method: 'email',
    });
  });

  it('keeps the authenticator first when TOTP is on too', async () => {
    const send = vi.spyOn(api, 'sendLoginEmailCode');
    render(
      <SecondFactorStep
        challenge={{
          pending_token: 'p-2',
          methods: ['totp', 'recovery', 'email'],
          expires_in: 300,
        }}
        onDone={vi.fn()}
        onRestart={vi.fn()}
      />
    );
    expect(screen.getByLabelText(t('auth.secondFactor.codeLabel'))).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText(t('auth.emailCode.useEmail')));
    expect(screen.getByRole('button', { name: t('auth.emailCode.send') })).toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
  });
});

describe('settings', () => {
  it('is a tab of the Connection section', () => {
    expect(CONNECTION_TABS).toContain('email');
  });

  it('picks the channel that sends the sign-in mails', async () => {
    vi.spyOn(api, 'getEmailSignInSettings').mockResolvedValue({
      channel_id: null,
      available: false,
      channels: [{ id: 'email-1', name: 'Home SMTP', enabled: true, usable: true }],
    });
    const patch = vi.spyOn(api, 'patchEmailSignInSettings').mockResolvedValue({
      channel_id: 'email-1',
      available: true,
      channels: [{ id: 'email-1', name: 'Home SMTP', enabled: true, usable: true }],
    });
    render(<AccessEmailSignInPanel />);
    const select = await screen.findByLabelText(t('settings.access.emailSignIn.channel'));
    fireEvent.change(select, { target: { value: 'email-1' } });
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: t('settings.access.emailSignIn.save') }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith('email-1'));
    expect(await screen.findByText(t('settings.access.emailSignIn.ready'))).toBeInTheDocument();
  });

  it('verifies an address with the code sent to it', async () => {
    const empty: EmailFactorStatus = {
      applicable: true,
      available: true,
      address: null,
      otp_enabled: false,
      magic_link_enabled: false,
      pending_address: null,
    };
    vi.spyOn(api, 'getEmailFactor').mockResolvedValue(empty);
    const start = vi.spyOn(api, 'startEmailAddress').mockResolvedValue({
      sent: true,
      expires_in: 600,
      resend_in: 60,
      email_hint: 'a***@example.org',
    });
    const verify = vi
      .spyOn(api, 'verifyEmailAddress')
      .mockResolvedValue({ ...empty, address: 'alice@example.org' });
    render(<AccessEmailFactorPanel />);
    const ui = userEvent.setup();
    await ui.type(
      await screen.findByLabelText(t('settings.access.emailFactor.newAddress')),
      'alice@example.org'
    );
    await ui.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'secret pass');
    await ui.click(screen.getByRole('button', { name: t('settings.access.emailFactor.sendCode') }));
    await waitFor(() => expect(start).toHaveBeenCalled());
    expect(start.mock.calls[0][0]).toMatchObject({
      address: 'alice@example.org',
      password: 'secret pass',
    });
    await ui.type(await screen.findByLabelText(t('settings.access.emailFactor.code')), '654321');
    await ui.click(screen.getByRole('button', { name: t('settings.access.emailFactor.confirm') }));
    await waitFor(() => expect(verify).toHaveBeenCalledWith('654321'));
    expect(await screen.findByText('alice@example.org')).toBeInTheDocument();
  });

  it('says why nothing can be sent without an SMTP profile', async () => {
    vi.spyOn(api, 'getEmailFactor').mockResolvedValue({
      applicable: true,
      available: false,
      address: null,
      otp_enabled: false,
      magic_link_enabled: false,
    });
    render(<AccessEmailFactorPanel />);
    expect(
      await screen.findByText(t('settings.access.emailFactor.unavailable'))
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(t('settings.access.emailFactor.newAddress'))).toBeNull();
  });
});
