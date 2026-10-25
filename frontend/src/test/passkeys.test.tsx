import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { passkeyCeremonyErrorKey } from '../auth/passkeys';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthMe, AuthSessionInfo, PasskeyRow, SecondFactorChallenge } from '../auth/types';
import { AccessPasskeysPanel } from '../components/settings/AccessPasskeysPanel';
import i18n from '../i18n';

const library = vi.hoisted(() => ({
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
}));
vi.mock('@simplewebauthn/browser', () => library);

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const anonymous: AuthSessionInfo = {
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false, passkey: true },
  auth_compat: { basic: false },
};
const signedIn = (over: Partial<AuthSessionInfo> = {}): AuthSessionInfo => ({
  ...anonymous,
  authenticated: true,
  user: { id: 1, username: 'root', display_name: 'Root', role: 'admin' },
  via: 'lan',
  ...over,
});

const codedError = (status: number, code: string) =>
  new ApiError(code, status, { code, message: code, params: {} });

const OPTIONS = { challenge: 'abc' };
const CREDENTIAL = { id: 'cred', response: {} };

/** jsdom has neither: a secure context with `PublicKeyCredential` is a capable browser. */
function setBrowser({ secure, webauthn }: { secure: boolean; webauthn: boolean }) {
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true });
  if (webauthn) {
    Object.defineProperty(window, 'PublicKeyCredential', {
      value: function PublicKeyCredential() {},
      configurable: true,
      writable: true,
    });
  } else {
    Reflect.deleteProperty(window, 'PublicKeyCredential');
  }
}

beforeEach(() => {
  setBrowser({ secure: true, webauthn: true });
  library.startAuthentication.mockReset();
  library.startRegistration.mockReset();
});
afterEach(() => vi.restoreAllMocks());

async function renderLogin(session: AuthSessionInfo = anonymous) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(session);
  const user = userEvent.setup();
  render(
    <SessionProvider>
      <div data-testid="app">app</div>
    </SessionProvider>
  );
  await screen.findByLabelText(t('auth.username'));
  return user;
}

describe('sign in with a passkey', () => {
  it('runs the ceremony and opens the session', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockResolvedValue(OPTIONS);
    library.startAuthentication.mockResolvedValue(CREDENTIAL);
    const verify = vi.spyOn(api, 'passkeyLoginVerify').mockResolvedValue(signedIn());
    const user = await renderLogin();
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    expect(await screen.findByTestId('app')).toBeInTheDocument();
    expect(api.passkeyLoginOptions).toHaveBeenCalledWith();
    expect(library.startAuthentication).toHaveBeenCalledWith({ optionsJSON: OPTIONS });
    expect(verify).toHaveBeenCalledWith(CREDENTIAL, false);
  });

  it('is hidden when the server does not offer passkeys here', async () => {
    await renderLogin({
      ...anonymous,
      methods: { password: true, ingress: false, passkey: false },
    });
    expect(screen.queryByRole('button', { name: t('auth.passkey.signIn') })).toBeNull();
    // On https the silence would be a mystery: the page says why.
    expect(screen.getByRole('note')).toHaveTextContent(t('auth.passkey.unavailableHint'));
  });

  it('carries "keep me signed in" to the server', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockResolvedValue(OPTIONS);
    library.startAuthentication.mockResolvedValue(CREDENTIAL);
    const verify = vi.spyOn(api, 'passkeyLoginVerify').mockResolvedValue(signedIn());
    const user = await renderLogin();
    await user.click(screen.getByRole('checkbox', { name: t('auth.login.remember') }));
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    await screen.findByTestId('app');
    expect(verify).toHaveBeenCalledWith(CREDENTIAL, true);
  });

  it('shows the rate limit when the ceremony table is full', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockRejectedValue(
      new ApiError('Too Many Requests', 429, {
        code: 'auth_rate_limited',
        message: 'x',
        params: { retry_after: 30 },
      })
    );
    const user = await renderLogin();
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('errors.auth_rate_limited', { retry_after: 30 })
    );
    expect(library.startAuthentication).not.toHaveBeenCalled();
    // The form stays usable.
    expect(screen.getByLabelText(t('auth.password'))).toBeInTheDocument();
  });

  it('shows the generic rate limit when a proxy answers 429 without a code', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockRejectedValue(
      new ApiError('Too Many Requests', 429, 'slow down', 30)
    );
    const user = await renderLogin();
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('errors.access_rate_limited', { retry_after: 30 })
    );
  });

  it('is hidden on an insecure context, with an explanation', async () => {
    setBrowser({ secure: false, webauthn: false });
    await renderLogin();
    expect(screen.queryByRole('button', { name: t('auth.passkey.signIn') })).toBeNull();
    expect(screen.getByRole('note')).toHaveTextContent(t('auth.passkey.insecureHint'));
  });

  it('is hidden in a browser without WebAuthn', async () => {
    setBrowser({ secure: true, webauthn: false });
    await renderLogin();
    expect(screen.queryByRole('button', { name: t('auth.passkey.signIn') })).toBeNull();
  });

  it('says so when the prompt is cancelled and keeps the form', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockResolvedValue(OPTIONS);
    library.startAuthentication.mockRejectedValue(
      Object.assign(new Error('x'), {
        name: 'WebAuthnError',
        code: 'ERROR_PASSTHROUGH_SEE_CAUSE_PROPERTY',
        cause: Object.assign(new Error('x'), { name: 'NotAllowedError' }),
      })
    );
    const user = await renderLogin();
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('auth.passkey.errors.notAllowed'));
    expect(screen.getByLabelText(t('auth.password'))).toBeInTheDocument();
  });

  it('shows a server refusal', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockResolvedValue(OPTIONS);
    library.startAuthentication.mockResolvedValue(CREDENTIAL);
    vi.spyOn(api, 'passkeyLoginVerify').mockRejectedValue(codedError(401, 'auth_passkey_invalid'));
    const user = await renderLogin();
    await user.click(screen.getByRole('button', { name: t('auth.passkey.signIn') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_passkey_invalid'));
  });
});

describe('passkey as a second factor', () => {
  const challenge: SecondFactorChallenge = {
    pending_token: 'pending-token',
    methods: ['totp', 'recovery', 'passkey'],
    expires_in: 300,
  };

  async function secondStep(methods: SecondFactorChallenge['methods']) {
    vi.spyOn(api, 'authLogin').mockResolvedValue({
      ...anonymous,
      second_factor: { ...challenge, methods },
    });
    const user = await renderLogin();
    await user.type(screen.getByLabelText(t('auth.username')), 'root');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    await screen.findByLabelText(t('auth.secondFactor.codeLabel'));
    return user;
  }

  it('answers the pending login with an assertion', async () => {
    const options = vi.spyOn(api, 'passkeyLoginOptions').mockResolvedValue(OPTIONS);
    library.startAuthentication.mockResolvedValue(CREDENTIAL);
    const second = vi.spyOn(api, 'authLogin2fa').mockResolvedValue(signedIn());
    const user = await secondStep(['totp', 'recovery', 'passkey']);
    await user.click(screen.getByRole('button', { name: t('auth.passkey.useAsSecondFactor') }));
    expect(await screen.findByTestId('app')).toBeInTheDocument();
    expect(options).toHaveBeenCalledWith('pending-token');
    expect(second).toHaveBeenCalledWith({ pending_token: 'pending-token', passkey: CREDENTIAL });
  });

  it('is not offered when the account has no passkey', async () => {
    await secondStep(['totp', 'recovery']);
    expect(screen.queryByRole('button', { name: t('auth.passkey.useAsSecondFactor') })).toBeNull();
  });

  it('goes back to the password when the pending login is gone', async () => {
    vi.spyOn(api, 'passkeyLoginOptions').mockRejectedValue(
      codedError(401, 'auth_2fa_pending_invalid')
    );
    const user = await secondStep(['totp', 'recovery', 'passkey']);
    await user.click(screen.getByRole('button', { name: t('auth.passkey.useAsSecondFactor') }));
    expect(await screen.findByLabelText(t('auth.password'))).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(t('errors.auth_2fa_pending_invalid'));
  });
});

const me = (over: Partial<AuthMe> = {}): AuthMe => ({
  id: 1,
  username: 'root',
  display_name: 'Root',
  role: 'admin',
  provider: 'local',
  has_password: true,
  must_change_password: false,
  created_at: 1,
  ...over,
});

const row = (over: Partial<PasskeyRow> = {}): PasskeyRow => ({
  id: 7,
  name: 'Laptop',
  created_at: 1_700_000_000,
  last_used_at: null,
  transports: ['internal'],
  aaguid: '',
  backup_eligible: false,
  backed_up: false,
  ...over,
});

async function renderPanel(session: AuthSessionInfo = signedIn()) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(session);
  render(
    <SessionProvider>
      <AccessPasskeysPanel />
    </SessionProvider>
  );
  await screen.findByText(t('settings.access.passkeys.title'));
  return userEvent.setup();
}

describe('AccessPasskeysPanel', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getAuthMe').mockResolvedValue(me());
  });

  it('lists the passkeys with their dates', async () => {
    vi.spyOn(api, 'listPasskeys').mockResolvedValue([
      row(),
      row({ id: 8, name: 'Phone', last_used_at: 1_700_100_000, backup_eligible: true }),
    ]);
    await renderPanel();
    expect(await screen.findByText('Laptop')).toBeInTheDocument();
    expect(screen.getByText('Phone')).toBeInTheDocument();
    expect(
      screen.getByText(t('settings.access.passkeys.neverUsed'), { exact: false })
    ).toBeInTheDocument();
    expect(screen.getByText(t('settings.access.passkeys.synced'))).toBeInTheDocument();
  });

  it('adds a passkey: password and name, the ceremony, then the server', async () => {
    const list = vi
      .spyOn(api, 'listPasskeys')
      .mockResolvedValueOnce([])
      .mockResolvedValue([row({ name: 'Laptop' })]);
    const options = vi.spyOn(api, 'passkeyRegisterOptions').mockResolvedValue(OPTIONS);
    library.startRegistration.mockResolvedValue(CREDENTIAL);
    const verify = vi.spyOn(api, 'passkeyRegisterVerify').mockResolvedValue(row());
    const user = await renderPanel();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.passkeys.add') })
    );
    // Without TOTP there is no code field.
    expect(screen.queryByLabelText(t('auth.totp.codeField'))).toBeNull();
    await user.type(screen.getByLabelText(t('settings.access.passkeys.name')), ' Laptop ');
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('settings.access.passkeys.addSubmit') }));
    expect(await screen.findByText('Laptop')).toBeInTheDocument();
    expect(options).toHaveBeenCalledWith('correct horse battery', undefined);
    expect(library.startRegistration).toHaveBeenCalledWith({ optionsJSON: OPTIONS });
    expect(verify).toHaveBeenCalledWith(CREDENTIAL, 'Laptop');
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('asks for a code too when TOTP is on, and warns about recovery codes', async () => {
    vi.mocked(api.getAuthMe).mockResolvedValue(me({ totp_enabled: true }));
    vi.spyOn(api, 'listPasskeys').mockResolvedValue([]);
    const options = vi.spyOn(api, 'passkeyRegisterOptions').mockResolvedValue(OPTIONS);
    library.startRegistration.mockResolvedValue(CREDENTIAL);
    vi.spyOn(api, 'passkeyRegisterVerify').mockResolvedValue(row());
    const user = await renderPanel();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.passkeys.add') })
    );
    expect(screen.getByText(t('settings.access.passkeys.codeHelp'))).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: t('settings.access.passkeys.addSubmit') });
    await user.type(screen.getByLabelText(t('settings.access.passkeys.name')), 'Key');
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText(t('auth.totp.codeField')), ' 123456 ');
    await user.click(submit);
    await waitFor(() => expect(options).toHaveBeenCalledWith('pw', '123456'));
  });

  it('maps an already registered authenticator and clears the proofs', async () => {
    vi.spyOn(api, 'listPasskeys').mockResolvedValue([]);
    vi.spyOn(api, 'passkeyRegisterOptions').mockResolvedValue(OPTIONS);
    library.startRegistration.mockRejectedValue(
      Object.assign(new Error('x'), {
        name: 'InvalidStateError',
        code: 'ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED',
      })
    );
    const user = await renderPanel();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.passkeys.add') })
    );
    await user.type(screen.getByLabelText(t('settings.access.passkeys.name')), 'Key');
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.click(screen.getByRole('button', { name: t('settings.access.passkeys.addSubmit') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('auth.passkey.errors.alreadyRegistered')
    );
    expect(screen.getByLabelText(t('auth.totp.currentPassword'))).toHaveValue('');
  });

  it('shows the server refusal for a missing code', async () => {
    vi.spyOn(api, 'listPasskeys').mockResolvedValue([]);
    vi.spyOn(api, 'passkeyRegisterOptions').mockRejectedValue(codedError(403, 'auth_2fa_required'));
    const user = await renderPanel();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.passkeys.add') })
    );
    await user.type(screen.getByLabelText(t('settings.access.passkeys.name')), 'Key');
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.click(screen.getByRole('button', { name: t('settings.access.passkeys.addSubmit') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_2fa_required'));
    expect(library.startRegistration).not.toHaveBeenCalled();
  });

  it('removes a passkey with the password', async () => {
    vi.spyOn(api, 'listPasskeys').mockResolvedValueOnce([row()]).mockResolvedValue([]);
    const remove = vi.spyOn(api, 'deletePasskey').mockResolvedValue({ ok: true });
    const user = await renderPanel();
    await user.click(
      await screen.findByRole('button', {
        name: t('settings.access.passkeys.remove', { name: 'Laptop' }),
      })
    );
    await user.type(
      screen.getByLabelText(t('settings.access.passkeys.removePassword', { name: 'Laptop' })),
      'pw'
    );
    await user.click(
      screen.getByRole('button', { name: t('settings.access.passkeys.removeSubmit') })
    );
    expect(await screen.findByText(t('settings.access.passkeys.none'))).toBeInTheDocument();
    expect(remove).toHaveBeenCalledWith(7, 'pw');
  });

  it('hides the add button, with a reason, where passkeys cannot run', async () => {
    vi.spyOn(api, 'listPasskeys').mockResolvedValue([row()]);
    await renderPanel(signedIn({ methods: { password: true, ingress: false, passkey: false } }));
    expect(await screen.findByTestId('passkeys-unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: t('settings.access.passkeys.add') })).toBeNull();
    // Removing a lost device's passkey still works.
    expect(screen.getByText('Laptop')).toBeInTheDocument();
  });

  it('explains under Home Assistant ingress', async () => {
    const list = vi.spyOn(api, 'listPasskeys').mockResolvedValue([]);
    await renderPanel(signedIn({ via: 'ingress' }));
    expect(await screen.findByTestId('passkeys-not-applicable')).toHaveTextContent(
      t('settings.access.passkeys.ingress')
    );
    expect(list).not.toHaveBeenCalled();
  });
});

describe('passkeyCeremonyErrorKey', () => {
  it('maps the library errors and the DOM exceptions under their cause', () => {
    expect(passkeyCeremonyErrorKey({ name: 'WebAuthnError', code: 'ERROR_CEREMONY_ABORTED' })).toBe(
      'cancelled'
    );
    expect(passkeyCeremonyErrorKey({ name: 'AbortError' })).toBe('cancelled');
    expect(
      passkeyCeremonyErrorKey({ name: 'WebAuthnError', cause: { name: 'NotAllowedError' } })
    ).toBe('notAllowed');
    expect(
      passkeyCeremonyErrorKey({
        name: 'WebAuthnError',
        code: 'ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED',
      })
    ).toBe('alreadyRegistered');
    expect(passkeyCeremonyErrorKey({ name: 'WebAuthnError', code: 'ERROR_X' })).toBe('failed');
    expect(passkeyCeremonyErrorKey(new ApiError('x', 500))).toBeNull();
    expect(passkeyCeremonyErrorKey('boom')).toBeNull();
  });
});
