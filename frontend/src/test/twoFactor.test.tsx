import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type {
  AuthMe,
  AuthSessionInfo,
  AuthSettings,
  SecondFactorChallenge,
  TotpEnrolment,
  TotpStatus,
} from '../auth/types';
import { AccessSecurityPanel } from '../components/settings/AccessSecurityPanel';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const anonymous: AuthSessionInfo = {
  authenticated: false,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  auth_compat: { basic: false },
};

const signedIn = (over: Partial<AuthSessionInfo> = {}): AuthSessionInfo => ({
  ...anonymous,
  authenticated: true,
  user: { id: 1, username: 'root', display_name: 'Root', role: 'admin' },
  via: 'lan',
  ...over,
});

const challenge: SecondFactorChallenge = {
  pending_token: 'pending-token',
  methods: ['totp', 'recovery'],
  expires_in: 300,
};

const codedError = (status: number, code: string) =>
  new ApiError(code, status, { code, message: code, params: {} });

describe('login second step', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getAuthSession').mockResolvedValue(anonymous);
    vi.spyOn(api, 'authLogin').mockResolvedValue({ ...anonymous, second_factor: challenge });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  async function passwordStep() {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <div data-testid="app">app</div>
      </SessionProvider>
    );
    await user.type(await screen.findByLabelText(t('auth.username')), 'root');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    await screen.findByLabelText(t('auth.secondFactor.codeLabel'));
    return user;
  }

  it('asks for a code after the password and opens the session with it', async () => {
    const second = vi.spyOn(api, 'authLogin2fa').mockResolvedValue(signedIn());
    const user = await passwordStep();
    expect(api.authLogin).toHaveBeenCalledWith({
      username: 'root',
      password: 'correct horse battery',
    });
    await user.type(screen.getByLabelText(t('auth.secondFactor.codeLabel')), '123456');
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.submit') }));
    expect(await screen.findByTestId('app')).toBeInTheDocument();
    expect(second).toHaveBeenCalledWith({ pending_token: 'pending-token', code: '123456' });
  });

  it('accepts a recovery code through the toggle', async () => {
    const second = vi.spyOn(api, 'authLogin2fa').mockResolvedValue(signedIn());
    const user = await passwordStep();
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.useRecovery') }));
    await user.type(screen.getByLabelText(t('auth.secondFactor.recoveryLabel')), 'abcd-efgh-jkmn');
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.submit') }));
    await screen.findByTestId('app');
    expect(second).toHaveBeenCalledWith({ pending_token: 'pending-token', code: 'abcd-efgh-jkmn' });
  });

  it('shows a wrong code and keeps the step', async () => {
    vi.spyOn(api, 'authLogin2fa').mockRejectedValue(codedError(401, 'auth_2fa_invalid'));
    const user = await passwordStep();
    await user.type(screen.getByLabelText(t('auth.secondFactor.codeLabel')), '000000');
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.submit') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_2fa_invalid'));
    expect(screen.getByLabelText(t('auth.secondFactor.codeLabel'))).toHaveValue('');
  });

  it('goes back to the password when the server forgot the pending login', async () => {
    vi.spyOn(api, 'authLogin2fa').mockRejectedValue(codedError(401, 'auth_2fa_pending_invalid'));
    const user = await passwordStep();
    await user.type(screen.getByLabelText(t('auth.secondFactor.codeLabel')), '123456');
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.submit') }));
    expect(await screen.findByLabelText(t('auth.password'))).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(t('errors.auth_2fa_pending_invalid'));
  });

  it('goes back to the password when the pending login times out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.authLogin).mockResolvedValue({
      ...anonymous,
      second_factor: { ...challenge, expires_in: 60 },
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <SessionProvider>
        <div />
      </SessionProvider>
    );
    await user.type(await screen.findByLabelText(t('auth.username')), 'root');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    await screen.findByLabelText(t('auth.secondFactor.codeLabel'));
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    expect(await screen.findByLabelText(t('auth.password'))).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(t('auth.secondFactor.expired'));
  });

  it('keeps the expiry deadline when the language changes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.authLogin).mockResolvedValue({
      ...anonymous,
      second_factor: { ...challenge, expires_in: 60 },
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <SessionProvider>
        <div />
      </SessionProvider>
    );
    await user.type(await screen.findByLabelText(t('auth.username')), 'root');
    await user.type(screen.getByLabelText(t('auth.password')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('auth.login.submit') }));
    await screen.findByLabelText(t('auth.secondFactor.codeLabel'));
    act(() => {
      vi.advanceTimersByTime(40_000);
    });
    const language = i18n.language;
    await act(async () => {
      await i18n.changeLanguage(language.startsWith('fr') ? 'en' : 'fr');
    });
    act(() => {
      vi.advanceTimersByTime(25_000);
    });
    try {
      expect(await screen.findByLabelText(t('auth.password'))).toBeInTheDocument();
    } finally {
      await i18n.changeLanguage(language);
    }
  });

  it('shows the rate limit with its delay', async () => {
    vi.spyOn(api, 'authLogin2fa').mockRejectedValue(
      new ApiError(
        'limited',
        429,
        { code: 'auth_rate_limited', message: '', params: { retry_after: 7 } },
        7
      )
    );
    const user = await passwordStep();
    await user.type(screen.getByLabelText(t('auth.secondFactor.codeLabel')), '123456');
    await user.click(screen.getByRole('button', { name: t('auth.secondFactor.submit') }));
    expect(await screen.findByRole('alert')).toHaveTextContent('7');
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

const status = (over: Partial<TotpStatus> = {}): TotpStatus => ({
  enabled: false,
  pending: false,
  recovery_codes_left: 0,
  enrolment_required: false,
  ...over,
});

const enrolment: TotpEnrolment = {
  secret: 'JBSWY3DPEHPK3PXP',
  otpauth_uri: 'otpauth://totp/Meshloom:root?secret=JBSWY3DPEHPK3PXP&issuer=Meshloom',
  issuer: 'Meshloom',
  digits: 6,
  period: 30,
};

const CODES = ['aaaa-bbbb-cccc', 'dddd-eeee-ffff'];

async function renderPanel(session: AuthSessionInfo = signedIn()) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(session);
  render(
    <SessionProvider>
      <AccessSecurityPanel />
    </SessionProvider>
  );
  await screen.findByText(t('settings.access.security.title'));
}

describe('AccessSecurityPanel', () => {
  let settings: AuthSettings;
  beforeEach(() => {
    settings = { require_2fa_for_admins: false } as AuthSettings;
    vi.spyOn(api, 'getAuthMe').mockResolvedValue(me());
    vi.spyOn(api, 'getAuthSettings').mockImplementation(async () => settings);
  });
  afterEach(() => vi.restoreAllMocks());

  it('enrols: password, QR from the URI and the manual key, code, recovery codes shown once', async () => {
    const getStatus = vi
      .spyOn(api, 'getTotpStatus')
      .mockResolvedValueOnce(status())
      .mockResolvedValue(status({ enabled: true, recovery_codes_left: 2 }));
    const enrol = vi.spyOn(api, 'enrolTotp').mockResolvedValue(enrolment);
    const confirm = vi.spyOn(api, 'confirmTotp').mockResolvedValue({ recovery_codes: CODES });
    const fetchFn = vi.spyOn(globalThis, 'fetch');
    await renderPanel();
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: t('settings.access.security.enable') })
    );
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: t('auth.totp.start') }));
    expect(enrol).toHaveBeenCalledWith('correct horse battery');

    expect(await screen.findByText('JBSW Y3DP EHPK 3PXP')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('totp-qr').querySelector('svg')).not.toBeNull());
    await user.type(screen.getByLabelText(t('auth.totp.confirmCode')), '123456');
    await user.click(screen.getByRole('button', { name: t('auth.totp.confirm') }));
    expect(confirm).toHaveBeenCalledWith('123456');

    // Shown once, with the warning; Done waits for the acknowledgement.
    expect(await screen.findByText('aaaa-bbbb-cccc')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(t('auth.recoveryCodes.warning'));
    const done = screen.getByRole('button', { name: t('auth.recoveryCodes.done') });
    expect(done).toBeDisabled();
    await user.click(screen.getByLabelText(t('auth.recoveryCodes.saved')));
    await user.click(done);
    await waitFor(() => expect(screen.queryByText('aaaa-bbbb-cccc')).toBeNull());
    expect(getStatus).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(t('settings.access.security.regenerate'))).toBeInTheDocument();
    // The secret goes to no third-party service: no request left the page but the mocked API.
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it('shows a wrong first code and stays on the step', async () => {
    vi.spyOn(api, 'getTotpStatus').mockResolvedValue(status());
    vi.spyOn(api, 'enrolTotp').mockResolvedValue(enrolment);
    vi.spyOn(api, 'confirmTotp').mockRejectedValue(codedError(400, 'auth_2fa_invalid'));
    await renderPanel();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.security.enable') })
    );
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.click(screen.getByRole('button', { name: t('auth.totp.start') }));
    await user.type(await screen.findByLabelText(t('auth.totp.confirmCode')), '000000');
    await user.click(screen.getByRole('button', { name: t('auth.totp.confirm') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_2fa_invalid'));
    expect(screen.getByText('JBSW Y3DP EHPK 3PXP')).toBeInTheDocument();
  });

  it('regenerates the recovery codes with the password and a code', async () => {
    vi.spyOn(api, 'getTotpStatus').mockResolvedValue(
      status({ enabled: true, recovery_codes_left: 1 })
    );
    const regenerate = vi
      .spyOn(api, 'regenerateRecoveryCodes')
      .mockResolvedValue({ recovery_codes: CODES });
    await renderPanel();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.security.regenerate') })
    );
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.type(screen.getByLabelText(t('auth.totp.codeField')), '123456');
    await user.click(
      screen.getByRole('button', { name: t('settings.access.security.regenerateSubmit') })
    );
    expect(regenerate).toHaveBeenCalledWith('pw', '123456');
    expect(await screen.findByText('dddd-eeee-ffff')).toBeInTheDocument();
  });

  it('disables with the password and a code, and reports a refusal', async () => {
    vi.spyOn(api, 'getTotpStatus')
      .mockResolvedValueOnce(status({ enabled: true, recovery_codes_left: 3 }))
      .mockResolvedValue(status());
    const disable = vi
      .spyOn(api, 'disableTotp')
      .mockRejectedValueOnce(codedError(403, 'auth_2fa_invalid'))
      .mockResolvedValueOnce({ ok: true });
    await renderPanel();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('button', { name: t('settings.access.security.disable') })
    );
    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.type(screen.getByLabelText(t('auth.totp.codeField')), '111111');
    await user.click(
      screen.getByRole('button', { name: t('settings.access.security.disableSubmit') })
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.auth_2fa_invalid'));

    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.type(screen.getByLabelText(t('auth.totp.codeField')), 'aaaa-bbbb-cccc');
    await user.click(
      screen.getByRole('button', { name: t('settings.access.security.disableSubmit') })
    );
    await waitFor(() => expect(disable).toHaveBeenLastCalledWith('pw', 'aaaa-bbbb-cccc'));
    expect(
      await screen.findByRole('button', { name: t('settings.access.security.enable') })
    ).toBeInTheDocument();
  });

  it('says why it does not apply under Home Assistant ingress', async () => {
    const getStatus = vi.spyOn(api, 'getTotpStatus');
    vi.mocked(api.getAuthMe).mockResolvedValue(me({ provider: 'ingress', has_password: false }));
    await renderPanel(signedIn({ via: 'ingress' }));
    expect(await screen.findByTestId('totp-not-applicable')).toHaveTextContent(
      t('settings.access.security.ingress')
    );
    expect(getStatus).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: t('settings.access.security.enable') })).toBeNull();
  });

  it('lets an administrator require it for administrators', async () => {
    vi.spyOn(api, 'getTotpStatus').mockResolvedValue(status());
    const patch = vi.spyOn(api, 'patchAuthSettings').mockResolvedValue({
      require_2fa_for_admins: true,
    } as AuthSettings);
    await renderPanel();
    const user = userEvent.setup();
    const box = await screen.findByRole('checkbox', {
      name: t('settings.access.security.require'),
    });
    expect(box).not.toBeChecked();
    await user.click(box);
    expect(patch).toHaveBeenCalledWith({ require_2fa_for_admins: true });
    await waitFor(() => expect(box).toBeChecked());
  });

  it('hides the administrator rule from a member', async () => {
    vi.spyOn(api, 'getTotpStatus').mockResolvedValue(status());
    await renderPanel(
      signedIn({ user: { id: 2, username: 'm', display_name: 'M', role: 'member' } })
    );
    await screen.findByRole('button', { name: t('settings.access.security.enable') });
    expect(
      screen.queryByRole('checkbox', { name: t('settings.access.security.require') })
    ).toBeNull();
  });
});

describe('enrolment required for administrators', () => {
  afterEach(() => vi.restoreAllMocks());

  it('replaces the app with the enrolment until the factor is on', async () => {
    const required = signedIn({
      user: {
        id: 1,
        username: 'root',
        display_name: 'Root',
        role: 'admin',
        second_factor_enrolment_required: true,
      },
    });
    const session = vi
      .spyOn(api, 'getAuthSession')
      .mockResolvedValueOnce(required)
      .mockResolvedValue(signedIn());
    vi.spyOn(api, 'enrolTotp').mockResolvedValue(enrolment);
    vi.spyOn(api, 'confirmTotp').mockResolvedValue({ recovery_codes: CODES });
    render(
      <SessionProvider>
        <div data-testid="app">app</div>
      </SessionProvider>
    );
    const user = userEvent.setup();
    expect(await screen.findByText(t('auth.enrolmentRequired.title'))).toBeInTheDocument();
    expect(screen.queryByTestId('app')).toBeNull();

    await user.type(screen.getByLabelText(t('auth.totp.currentPassword')), 'pw');
    await user.click(screen.getByRole('button', { name: t('auth.totp.start') }));
    await user.type(await screen.findByLabelText(t('auth.totp.confirmCode')), '123456');
    await user.click(screen.getByRole('button', { name: t('auth.totp.confirm') }));
    await user.click(await screen.findByLabelText(t('auth.recoveryCodes.saved')));
    await user.click(screen.getByRole('button', { name: t('auth.recoveryCodes.done') }));

    expect(await screen.findByTestId('app')).toBeInTheDocument();
    expect(session).toHaveBeenCalledTimes(2);
  });
});
