import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthRole, AuthSessionInfo, AuthVia } from '../auth/types';
import {
  REMOTE_STATUS_POLL_MS,
  SettingsRemoteSection,
} from '../components/settings/SettingsRemoteSection';
import { ENROLL_POLL_MS } from '../components/settings/remote/RemoteEnrollDialog';
import {
  pluginsIsolationReduced,
  remoteBadge,
  untrustedText,
} from '../components/settings/remote/remoteShared';
import {
  REMOTE_LINK_STATES,
  type RemoteAccessRequest,
  type RemoteDevice,
  type RemoteDomainTest,
  type RemoteStatus,
} from '../components/settings/remote/remoteTypes';
import i18n from '../i18n';
import en from '../i18n/locales/slices/remote.en.json';
import fr from '../i18n/locales/slices/remote.fr.json';
import type { HealthStatus } from '../types';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));
vi.mock('../components/settings/PairingQr', () => ({
  default: ({ value }: { value: string }) => <div data-testid="qr-value">{value}</div>,
}));

const t = i18n.t.bind(i18n);
const NOW = Math.floor(Date.now() / 1000);
const ENROLL_URL = 'https://abc.meshloom.link/_meshloom/enroll#token=secret';

const sessionOf = (role: AuthRole, via: AuthVia = 'lan'): AuthSessionInfo => ({
  authenticated: true,
  setup_required: false,
  setup_code_required: false,
  methods: { password: true, ingress: false },
  user: { id: 1, username: 'root', display_name: 'Root', role },
  via,
});

const deviceOf = (over: Partial<RemoteDevice> = {}): RemoteDevice => ({
  id: 'dev-1',
  user_id: 1,
  kind: 'browser',
  label: 'Pixel',
  origin: 'qr',
  created_at: NOW - 100,
  pass_expires_at: NOW + 86_400,
  last_seen_at: NOW - 10,
  revoked_at: null,
  active: true,
  ...over,
});

const statusOf = (over: Partial<RemoteStatus> = {}): RemoteStatus => ({
  enabled: true,
  state: 'registered',
  key_fingerprint: 'AB12 CD34 EF56 7890',
  slug: 'abc',
  hostname: 'abc.meshloom.link',
  relay_url: 'https://relay.meshloom.link',
  registered_at: NOW - 1000,
  last_error: null,
  relay_connected: true,
  link_status: 'connected',
  gate: { gen: 1, revoked: 0 },
  devices: [],
  ...over,
});

const OFF = statusOf({
  enabled: false,
  state: 'disabled',
  slug: null,
  hostname: null,
  registered_at: null,
  relay_connected: false,
  link_status: null,
  key_fingerprint: null,
});

const SAFE_HEALTH = { plugins_isolation: 'bwrap', plugins_disabled_source: null } as HealthStatus;
const REDUCED_HEALTH = { plugins_isolation: 'none', plugins_disabled_source: null } as HealthStatus;

async function renderAs(
  role: AuthRole,
  { via = 'lan', health = SAFE_HEALTH }: { via?: AuthVia; health?: HealthStatus | null } = {}
) {
  vi.spyOn(api, 'getAuthSession').mockResolvedValue(sessionOf(role, via));
  const onOpenSection = vi.fn();
  render(
    <SessionProvider>
      <SettingsRemoteSection health={health} onOpenSection={onOpenSection} />
    </SessionProvider>
  );
  await waitFor(() => expect(api.getRemote).toHaveBeenCalled());
  return { onOpenSection };
}

describe('remoteBadge', () => {
  it('maps every state to a badge with a label and a help in en and fr', () => {
    const ids = [
      remoteBadge(OFF).id,
      remoteBadge(statusOf({ state: 'unregistered', slug: null, link_status: null })).id,
      ...REMOTE_LINK_STATES.map((link) => remoteBadge(statusOf({ link_status: link })).id),
      remoteBadge(statusOf({ link_status: 'something_new' })).id,
    ];
    expect(new Set(ids).size).toBe(ids.length);
    for (const locale of [en, fr]) {
      const remote = locale.settings.remote as {
        badge: Record<string, string>;
        badgeHelp: Record<string, string>;
      };
      for (const id of ids) {
        expect(remote.badge[id], id).toBeTruthy();
        expect(remote.badgeHelp[id], id).toBeTruthy();
      }
    }
  });

  it('offers the right action', () => {
    expect(remoteBadge(OFF).action).toBe('enable');
    expect(remoteBadge(statusOf({ state: 'unregistered' })).action).toBe('enable');
    expect(remoteBadge(statusOf({ link_status: 'replaced' })).action).toBe('rotate');
    expect(remoteBadge(statusOf({ link_status: 'update_required' })).action).toBe('update');
    expect(remoteBadge(statusOf({ link_status: 'not_registered' })).action).toBe('restart');
    expect(remoteBadge(statusOf({ link_status: 'connected' })).action).toBeNull();
    // Registered with no link state yet: connecting.
    expect(remoteBadge(statusOf({ link_status: null })).id).toBe('connecting');
  });
});

describe('pluginsIsolationReduced', () => {
  it('mirrors the server rule', () => {
    expect(pluginsIsolationReduced(null)).toBe(true);
    expect(pluginsIsolationReduced(REDUCED_HEALTH)).toBe(true);
    expect(
      pluginsIsolationReduced({
        plugins_isolation: 'none',
        plugins_disabled_source: 'paused',
      } as HealthStatus)
    ).toBe(true);
    expect(
      pluginsIsolationReduced({
        plugins_isolation: 'none',
        plugins_disabled_source: 'env',
      } as HealthStatus)
    ).toBe(false);
    expect(pluginsIsolationReduced(SAFE_HEALTH)).toBe(false);
  });
});

describe('untrustedText', () => {
  it('drops control and direction characters and shortens', () => {
    expect(untrustedText('Evil‮gnp.exe\u0000​ ok')).toBe('Evil gnp.exe ok');
    expect(untrustedText('a'.repeat(400)).length).toBe(160);
  });
});

describe('SettingsRemoteSection', () => {
  beforeEach(() => {
    vi.spyOn(api, 'listAuthUsers').mockResolvedValue([]);
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows one actionable line and links the Remote docs for the details', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    await renderAs('admin');
    const note = await screen.findByTestId('remote-privacy-note');
    expect(note).toHaveTextContent(t('settings.remote.about.secure'));
    const link = within(note).getByRole('link', { name: t('settings.remote.about.learnMore') });
    expect(link).toHaveAttribute('href', 'https://meshloom.app/docs/deep/remote/');
  });

  it('enables, then enrols a phone by QR without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    const enable = vi.spyOn(api, 'enableRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'issueRemoteEnroll').mockResolvedValue({
      url: ENROLL_URL,
      expires_at: NOW + 900,
    });
    await renderAs('admin');
    expect(await screen.findByTestId('remote-badge')).toHaveTextContent(
      t('settings.remote.badge.off')
    );

    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.actions.enable') }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByTestId('remote-enable-isolation-warning')).toBeNull();
    // The details of what the relay sees live in the docs, not in the dialog (MSHL-449).
    expect(within(dialog).queryByText(/TwinRocket/)).toBeNull();
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.remote.enable.confirm') })
    );
    await waitFor(() => expect(enable).toHaveBeenCalledTimes(1));

    expect(await screen.findByTestId('remote-hostname')).toHaveTextContent(
      'https://abc.meshloom.link'
    );
    expect(screen.getByTestId('remote-fingerprint')).toHaveTextContent('AB12 CD34 EF56 7890');
    expect(screen.getByTestId('remote-badge')).toHaveTextContent(
      t('settings.remote.badge.connected')
    );

    getRemote.mockResolvedValue(statusOf());
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.enroll.show') }));
    expect(await screen.findByTestId('qr-value')).toHaveTextContent(ENROLL_URL);
    expect(screen.getByTestId('remote-enroll-countdown')).toHaveTextContent('15:00');

    // The phone scans the code: the next poll sees the new device.
    getRemote.mockResolvedValue(statusOf({ devices: [deviceOf({ id: 'new-phone' })] }));
    await act(async () => {
      vi.advanceTimersByTime(ENROLL_POLL_MS);
    });
    expect(await screen.findByTestId('remote-enroll-done')).toBeInTheDocument();
    expect(screen.queryByTestId('qr-value')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.enroll.close') }));
    const list = await screen.findByTestId('remote-devices-list');
    expect(within(list).getByText('Pixel')).toBeInTheDocument();
  });

  it('still reports the enrolment when the section poll sees the new device first', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'issueRemoteEnroll').mockResolvedValue({
      url: ENROLL_URL,
      expires_at: NOW + 900,
    });
    await renderAs('admin');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(13_000);
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.enroll.show') }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('qr-value')).toHaveTextContent(ENROLL_URL);

    // The phone enrols at about 14 s; the section poll (15 s) sees it before the
    // dialog's next poll (16 s) does.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    getRemote.mockResolvedValue(statusOf({ devices: [deviceOf({ id: 'new-phone' })] }));
    const before = getRemote.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_POLL_MS - 14_000);
    });
    expect(getRemote.mock.calls.length).toBeGreaterThan(before);
    expect(screen.queryByTestId('remote-enroll-done')).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ENROLL_POLL_MS);
    });
    expect(screen.getByTestId('remote-enroll-done')).toBeInTheDocument();
    expect(screen.queryByTestId('qr-value')).toBeNull();
  });

  it("does not report another account's QR enrolment as this one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'issueRemoteEnroll').mockResolvedValue({
      url: ENROLL_URL,
      expires_at: NOW + 900,
    });
    await renderAs('admin');
    fireEvent.click(await screen.findByRole('button', { name: t('settings.remote.enroll.show') }));
    expect(await screen.findByTestId('qr-value')).toBeInTheDocument();

    getRemote.mockResolvedValue(
      statusOf({ devices: [deviceOf({ id: 'other-phone', user_id: 2 })] })
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ENROLL_POLL_MS);
    });
    expect(screen.queryByTestId('remote-enroll-done')).toBeNull();
    expect(screen.getByTestId('qr-value')).toBeInTheDocument();

    getRemote.mockResolvedValue(
      statusOf({
        devices: [deviceOf({ id: 'other-phone', user_id: 2 }), deviceOf({ id: 'my-phone' })],
      })
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ENROLL_POLL_MS);
    });
    expect(await screen.findByTestId('remote-enroll-done')).toBeInTheDocument();
  });

  it('does not report the device just enrolled again for the next code', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    const issue = vi
      .spyOn(api, 'issueRemoteEnroll')
      .mockResolvedValue({ url: ENROLL_URL, expires_at: NOW + 900 });
    await renderAs('member');
    fireEvent.click(await screen.findByRole('button', { name: t('settings.remote.enroll.show') }));
    expect(await screen.findByTestId('qr-value')).toBeInTheDocument();
    getRemote.mockResolvedValue(statusOf({ devices: [deviceOf({ id: 'phone-1' })] }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ENROLL_POLL_MS);
    });
    expect(await screen.findByTestId('remote-enroll-done')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.enroll.another') }));
    expect(await screen.findByTestId('qr-value')).toBeInTheDocument();
    expect(issue).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ENROLL_POLL_MS);
    });
    expect(screen.queryByTestId('remote-enroll-done')).toBeNull();
    expect(screen.getByTestId('qr-value')).toBeInTheDocument();
  });

  it('hides the QR code once the enrolment code expires', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'issueRemoteEnroll').mockResolvedValue({ url: ENROLL_URL, expires_at: NOW });
    await renderAs('member');
    fireEvent.click(await screen.findByRole('button', { name: t('settings.remote.enroll.show') }));
    expect(await screen.findByTestId('qr-value')).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(15 * 60 * 1000 + 1000);
    });
    expect(await screen.findByTestId('remote-enroll-expired')).toBeInTheDocument();
    expect(screen.queryByTestId('qr-value')).toBeNull();
  });

  it('blocks enabling until the reduced plugin isolation warning is acknowledged', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    const enable = vi.spyOn(api, 'enableRemote').mockResolvedValue(statusOf());
    await renderAs('admin', { health: REDUCED_HEALTH });
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.enable') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByTestId('remote-enable-isolation-warning')).toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', {
      name: t('settings.remote.enable.confirm'),
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox'));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(enable).toHaveBeenCalledTimes(1));
  });

  it.each(REMOTE_LINK_STATES)('shows a translated message for %s', async (link) => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf({ link_status: link }));
    await renderAs('admin');
    const badge = await screen.findByTestId('remote-badge');
    expect(badge).toHaveTextContent(t(`settings.remote.badge.${link}`));
    expect(screen.getByTestId('remote-badge-help')).toHaveTextContent(
      t(`settings.remote.badgeHelp.${link}`)
    );
  });

  it('offers the action that fixes the state', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf({ link_status: 'update_required' }));
    const { onOpenSection } = await renderAs('admin');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.update') })
    );
    expect(onOpenSection).toHaveBeenCalledWith('updates');
  });

  it('rotates the key after a confirmation', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf({ link_status: 'replaced' }));
    const rotate = vi
      .spyOn(api, 'rotateRemote')
      .mockResolvedValue(statusOf({ key_fingerprint: '1111 2222 3333 4444', slug: 'xyz' }));
    await renderAs('admin');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.rotate') })
    );
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.remote.confirm.rotate.confirm') })
    );
    await waitFor(() => expect(rotate).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('remote-fingerprint')).toHaveTextContent(
      '1111 2222 3333 4444'
    );
  });

  it('shows a member no administrator control', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    await renderAs('member');
    expect(await screen.findByText(t('settings.remote.status.askAdmin'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: t('settings.remote.actions.enable') })).toBeNull();
    expect(api.listAuthUsers).not.toHaveBeenCalled();
  });

  it('refuses enrolment through the relay and says so', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    await renderAs('member', { via: 'relay' });
    expect(await screen.findByTestId('remote-enroll-lan-only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: t('settings.remote.enroll.show') })).toBeNull();
  });

  it('revokes one device and every device, and the list follows', async () => {
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(
      statusOf({
        devices: [deviceOf(), deviceOf({ id: 'dev-2', label: 'Laptop', user_id: 2 })],
      })
    );
    const revoke = vi.spyOn(api, 'revokeRemoteDevice').mockResolvedValue({ gate_pushed: true });
    const revokeAll = vi
      .spyOn(api, 'revokeAllRemoteDevices')
      .mockResolvedValue({ gate_pushed: false });
    await renderAs('admin');
    const list = await screen.findByTestId('remote-devices-list');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);

    getRemote.mockResolvedValue(
      statusOf({
        devices: [
          deviceOf({ revoked_at: NOW, active: false }),
          deviceOf({ id: 'dev-2', label: 'Laptop', user_id: 2 }),
        ],
      })
    );
    fireEvent.click(
      within(list).getByRole('button', {
        name: t('settings.remote.devices.revokeNamed', { name: 'Pixel' }),
      })
    );
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: t('settings.remote.devices.revoke'),
      })
    );
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('dev-1'));
    await waitFor(() =>
      expect(
        within(screen.getByTestId('remote-devices-list')).getAllByRole('listitem')
      ).toHaveLength(1)
    );
    expect(screen.getByTestId('remote-devices-inactive')).toHaveTextContent('Pixel');

    getRemote.mockResolvedValue(
      statusOf({
        devices: [
          deviceOf({ revoked_at: NOW, active: false }),
          deviceOf({ id: 'dev-2', label: 'Laptop', user_id: 2, active: false }),
        ],
      })
    );
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.devices.revokeAll') }));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: t('settings.remote.devices.revokeAll'),
      })
    );
    await waitFor(() => expect(revokeAll).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(t('settings.remote.devices.none'))).toBeInTheDocument();
  });

  it('renders an access request user agent as escaped text, with the warning', async () => {
    const hostile: RemoteAccessRequest = {
      request_id: 'r1',
      code: 'ABCDE-FGHIJ',
      user_agent: '<img src=x onerror=alert(1)>‮<script>alert(2)</script> Firefox/1',
      created_at: NOW - 5,
      expires_at: NOW + 595,
    };
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([hostile]);
    const approve = vi
      .spyOn(api, 'approveRemoteAccessRequest')
      .mockResolvedValue(deviceOf({ origin: 'approval' }));
    await renderAs('member');

    const agent = await screen.findByTestId('remote-request-agent');
    expect(agent.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(agent.textContent).not.toContain('‮');
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByTestId('remote-requests-warning')).toHaveTextContent(
      t('settings.remote.requests.socialWarning')
    );
    // The code is never shown: the reader has to type what the device shows.
    expect(screen.getByTestId('remote-requests-list')).not.toHaveTextContent('ABCDE-FGHIJ');

    fireEvent.change(screen.getByLabelText(t('settings.remote.requests.code')), {
      target: { value: ' abcde-fghij ' },
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.requests.approve') }));
    await waitFor(() => expect(approve).toHaveBeenCalledWith('abcde-fghij', undefined));
  });

  it('shows the translated error of a refused approval', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
    vi.spyOn(api, 'approveRemoteAccessRequest').mockRejectedValue(
      new ApiError('x', 404, { code: 'remote_access_request_not_found', message: 'x' })
    );
    await renderAs('member');
    fireEvent.change(await screen.findByLabelText(t('settings.remote.requests.code')), {
      target: { value: 'WRONG-CODE1' },
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.remote.requests.approve') }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('errors.remote_access_request_not_found')
    );
  });

  describe('custom domain test', () => {
    const run = async (result: Partial<RemoteDomainTest>) => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      const test = vi.spyOn(api, 'testRemoteDomain').mockResolvedValue({
        result: 'ok',
        status_code: 301,
        target_host: 'abc.meshloom.link',
        detail: null,
        ...result,
      });
      await renderAs('admin');
      fireEvent.change(await screen.findByLabelText(t('settings.remote.domain.label')), {
        target: { value: ' chat.example.org ' },
      });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.domain.test') }));
      const box = await screen.findByTestId('remote-domain-result');
      expect(test).toHaveBeenCalledWith('chat.example.org');
      return box;
    };

    it('reports ok, no redirect, wrong target and unreachable with translated messages', async () => {
      const ok = await run({});
      expect(ok).toHaveAttribute('data-result', 'ok');
      expect(ok).toHaveTextContent(t('settings.remote.domain.result.ok.title'));
      cleanup();
      expect(await run({ result: 'no_redirect', status_code: 200 })).toHaveTextContent(
        t('settings.remote.domain.result.no_redirect.title')
      );
      cleanup();
      const wrong = await run({ result: 'wrong_target', target_host: 'example.com' });
      expect(wrong).toHaveTextContent('example.com');
      cleanup();
      const down = await run({ result: 'unreachable', detail: 'egress_unresolvable' });
      expect(down).toHaveTextContent(t('settings.remote.domain.reason.egress_unresolvable'));
    });

    it('is for administrators of a registered instance only', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      await renderAs('member');
      await screen.findByTestId('remote-badge');
      expect(screen.queryByLabelText(t('settings.remote.domain.label'))).toBeNull();
    });

    it('shows the translated error of a refused test', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      vi.spyOn(api, 'testRemoteDomain').mockRejectedValue(
        new ApiError('x', 409, { code: 'remote_disabled', message: 'x' })
      );
      await renderAs('admin');
      fireEvent.change(await screen.findByLabelText(t('settings.remote.domain.label')), {
        target: { value: 'chat.example.org' },
      });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.domain.test') }));
      expect(await screen.findByRole('alert')).toHaveTextContent(t('errors.remote_disabled'));
    });

    it('shows the translated message of a rate limited test (429 rate_limited)', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      vi.spyOn(api, 'testRemoteDomain').mockRejectedValue(
        new ApiError('x', 429, { code: 'rate_limited', params: {} }, 5)
      );
      await renderAs('admin');
      fireEvent.change(await screen.findByLabelText(t('settings.remote.domain.label')), {
        target: { value: 'chat.example.org' },
      });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.domain.test') }));
      const alert = await screen.findByRole('alert');
      expect(alert).toHaveTextContent(t('errors.rate_limited'));
      expect(t('errors.rate_limited')).not.toBe('errors.rate_limited');
    });
  });
});
