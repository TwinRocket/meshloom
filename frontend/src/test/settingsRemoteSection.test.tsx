import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { SessionProvider } from '../auth/SessionProvider';
import type { AuthRole, AuthSessionInfo, AuthVia } from '../auth/types';
import {
  REMOTE_STATUS_POLL_MS,
  REMOTE_STATUS_SETTLING_POLL_MS,
  REMOTE_STATUS_STUCK_MS,
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
  community_enabled: true,
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
      remoteBadge({ ...OFF, community_enabled: false }).id,
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

  it('tells the monthly quota apart, in both languages', () => {
    expect(remoteBadge(statusOf({ link_status: 'quota_exceeded' }))).toEqual({
      id: 'quota_exceeded',
      tone: 'warn',
      action: null,
    });
    expect(en.settings.remote.badgeHelp.quota_exceeded).toContain('1st');
    expect(fr.settings.remote.badgeHelp.quota_exceeded).toContain('1er');
    expect(en.settings.remote.badgeHelp.paused).not.toMatch(/quota/i);
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

  it('needs Community: off it cannot be turned on, on it is paused (MSHL-445)', () => {
    expect(remoteBadge({ ...OFF, community_enabled: false })).toEqual({
      id: 'needs_community',
      tone: 'neutral',
      action: 'community',
    });
    for (const over of [
      { link_status: 'connected' },
      { link_status: 'community_off' },
      { state: 'unregistered' as const, slug: null, link_status: null },
    ]) {
      expect(remoteBadge(statusOf({ ...over, community_enabled: false }))).toEqual({
        id: 'community_off',
        tone: 'warn',
        action: 'community',
      });
    }
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
    vi.spyOn(api, 'getRemoteAlertEmail').mockResolvedValue({ state: 'none' });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('asks for two-factor authentication, never a passkey, to protect an account', () => {
    // A passkey leaves the password valid on its own: only two-factor stops a password alone.
    const cases = [
      [en, /two-factor authentication/],
      [fr, /double authentification/],
    ] as const;
    for (const [slice, expected] of cases) {
      const remote = slice.settings.remote;
      const notices = slice.settings.access.securityNotice;
      for (const line of [
        remote.about.secure,
        remote.enable.isolationWarning,
        notices.remote_reduced_plugin_isolation,
      ]) {
        expect(line).toMatch(expected);
        expect(line).not.toMatch(/passkey|clé d.accès/i);
      }
    }
  });

  it('shows one actionable line and links the Remote docs for the details', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    await renderAs('admin');
    const note = await screen.findByTestId('remote-privacy-note');
    expect(note).toHaveTextContent(t('settings.remote.about.secure'));
    const link = within(note).getByRole('link', { name: t('settings.remote.about.learnMore') });
    expect(link).toHaveAttribute('href', 'https://meshloom.app/docs/deep/remote/');
    // One line, one link: what the relay sees is in the docs only (frontend/AGENTS.md).
    expect(note.tagName).toBe('P');
    expect(within(note).getAllByRole('link')).toHaveLength(1);
    expect(note).toHaveTextContent(
      `${t('settings.remote.about.secure')} ${t('settings.remote.about.learnMore')}`
    );
  });

  it('opens a two-line enable dialog when plugins are sandboxed', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    await renderAs('admin', { health: SAFE_HEALTH });
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.enable') })
    );
    const dialog = await screen.findByRole('dialog');
    // Title and help only: no relay paragraph, no checkbox (frontend/AGENTS.md "Enable").
    expect(dialog.querySelectorAll('p')).toHaveLength(1);
    expect(dialog).toHaveTextContent(t('settings.remote.enable.help'));
    expect(within(dialog).queryByRole('checkbox')).toBeNull();
    expect(
      within(dialog).getByRole('button', { name: t('settings.remote.enable.confirm') })
    ).toBeEnabled();
  });

  it('follows the link while it settles, then goes back to the slow refresh', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const connecting = statusOf({ relay_connected: false, link_status: 'connecting' });
    const getRemote = vi.spyOn(api, 'getRemote').mockResolvedValue(OFF);
    vi.spyOn(api, 'enableRemote').mockResolvedValue(connecting);
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
    await renderAs('admin');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.enable') })
    );
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: t('settings.remote.enable.confirm'),
      })
    );
    expect(await screen.findByText(t('settings.remote.requests.offline'))).toBeInTheDocument();
    expect(screen.getByTestId('remote-badge')).toHaveAttribute('data-badge', 'connecting');

    getRemote.mockResolvedValue(statusOf());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS);
    });
    expect(screen.getByTestId('remote-badge')).toHaveAttribute('data-badge', 'connected');
    expect(screen.queryByText(t('settings.remote.requests.offline'))).toBeNull();

    const settled = getRemote.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_POLL_MS - 1_000);
    });
    expect(getRemote.mock.calls.length).toBe(settled);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(getRemote.mock.calls.length).toBe(settled + 1);
  });

  it('follows a link that is coming back, without a tight poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getRemote = vi
      .spyOn(api, 'getRemote')
      .mockResolvedValue(statusOf({ relay_connected: false, link_status: 'reconnecting' }));
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
    await renderAs('admin');
    expect(await screen.findByTestId('remote-badge')).toHaveAttribute('data-badge', 'reconnecting');
    expect(REMOTE_STATUS_SETTLING_POLL_MS).toBeGreaterThanOrEqual(2_000);
    const before = getRemote.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS - 500);
    });
    expect(getRemote.mock.calls.length).toBe(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(getRemote.mock.calls.length).toBe(before + 1);
  });

  it('lets a status slower than the fast refresh land, with one request at a time', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
    const getRemote = vi
      .spyOn(api, 'getRemote')
      .mockResolvedValue(statusOf({ relay_connected: false, link_status: 'connecting' }));
    await renderAs('admin');
    expect(await screen.findByTestId('remote-badge')).toHaveAttribute('data-badge', 'connecting');

    const slow = REMOTE_STATUS_SETTLING_POLL_MS + 500;
    getRemote.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(statusOf()), slow))
    );
    const before = getRemote.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS);
    });
    expect(getRemote.mock.calls.length).toBe(before + 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS);
    });
    expect(getRemote.mock.calls.length).toBe(before + 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(getRemote.mock.calls.length).toBe(before + 1);
    expect(screen.getByTestId('remote-badge')).toHaveAttribute('data-badge', 'connected');
  });

  it('asks again when a status request never answers', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
    const getRemote = vi
      .spyOn(api, 'getRemote')
      .mockResolvedValue(statusOf({ relay_connected: false, link_status: 'connecting' }));
    await renderAs('admin');
    await screen.findByTestId('remote-badge');

    getRemote.mockImplementation(() => new Promise(() => undefined));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS);
    });
    const hung = getRemote.mock.calls.length;
    getRemote.mockResolvedValue(statusOf());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_STUCK_MS - 1_000);
    });
    expect(getRemote.mock.calls.length).toBe(hung);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS + 1_000);
    });
    expect(getRemote.mock.calls.length).toBe(hung + 1);
    expect(screen.getByTestId('remote-badge')).toHaveAttribute('data-badge', 'connected');
  });

  it('pauses the fast refresh while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'listRemoteAccessRequests').mockResolvedValue([]);
    const getRemote = vi
      .spyOn(api, 'getRemote')
      .mockResolvedValue(statusOf({ relay_connected: false, link_status: 'connecting' }));
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    await renderAs('admin');
    await screen.findByTestId('remote-badge');
    const before = getRemote.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS * 3);
    });
    expect(getRemote.mock.calls.length).toBe(before);

    hidden.mockReturnValue(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REMOTE_STATUS_SETTLING_POLL_MS);
    });
    expect(getRemote.mock.calls.length).toBe(before + 1);
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
    const warning = within(dialog).getByTestId('remote-enable-isolation-warning');
    // One warning line and the acknowledgement, nothing more.
    expect(warning.querySelectorAll('p')).toHaveLength(1);
    expect(warning).toHaveTextContent(t('settings.remote.enable.isolationWarning'));
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

  it('sends to the Community settings instead of turning Remote on while Community is off', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue({ ...OFF, community_enabled: false });
    const enable = vi.spyOn(api, 'enableRemote');
    const { onOpenSection } = await renderAs('admin');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.remote.actions.community') })
    );
    expect(onOpenSection).toHaveBeenCalledWith('community');
    expect(screen.queryByRole('button', { name: t('settings.remote.actions.enable') })).toBeNull();
    expect(screen.getByTestId('remote-badge-help')).toHaveTextContent(
      t('settings.remote.badgeHelp.needs_community')
    );
    expect(enable).not.toHaveBeenCalled();
  });

  it('shows a Community attestation refusal as a translated sentence', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(
      statusOf({ state: 'unregistered', slug: null, last_error: 'remote_radio_not_heard' })
    );
    await renderAs('admin');
    expect(await screen.findByTestId('remote-last-error')).toHaveTextContent(
      t('errors.remote_radio_not_heard')
    );
  });

  it('shows another registry error as its raw code', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(
      statusOf({ state: 'unregistered', slug: null, last_error: 'registration_paused' })
    );
    await renderAs('admin');
    expect(await screen.findByTestId('remote-last-error')).toHaveTextContent('registration_paused');
  });

  it('shows an enabled Remote as paused while Community is off', async () => {
    vi.spyOn(api, 'getRemote').mockResolvedValue(
      statusOf({ community_enabled: false, link_status: 'community_off', relay_connected: false })
    );
    await renderAs('admin');
    expect(await screen.findByTestId('remote-badge')).toHaveAttribute(
      'data-badge',
      'community_off'
    );
    expect(screen.getByRole('button', { name: t('settings.remote.actions.disable') })).toBeTruthy();
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

  describe('alert e-mail', () => {
    const field = () => screen.findByLabelText(t('settings.remote.alertEmail.label'));

    it('shows the state, never an address, and sets a new one', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      vi.spyOn(api, 'getRemoteAlertEmail').mockResolvedValue({ state: 'none' });
      const set = vi.spyOn(api, 'setRemoteAlertEmail').mockResolvedValue({ state: 'pending' });
      await renderAs('admin');
      expect(await screen.findByTestId('remote-alert-email-state')).toHaveTextContent(
        t('settings.remote.alertEmail.state.none')
      );
      expect(
        screen.queryByRole('button', { name: t('settings.remote.alertEmail.remove') })
      ).toBeNull();
      const input = await field();
      expect(input).toHaveValue('');
      fireEvent.change(input, { target: { value: ' me@example.org ' } });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.alertEmail.save') }));
      await waitFor(() => expect(set).toHaveBeenCalledWith('me@example.org'));
      expect(await screen.findByTestId('remote-alert-email-state')).toHaveTextContent(
        t('settings.remote.alertEmail.state.pending')
      );
      expect(input).toHaveValue('');
    });

    it('says the relay keeps it, not Meshloom', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      await renderAs('admin');
      const heading = await screen.findByRole('heading', {
        name: t('settings.remote.alertEmail.title'),
      });
      const header = heading.parentElement as HTMLElement;
      expect(header).toHaveTextContent(t('settings.common.storedOnRelay'));
      expect(header).not.toHaveTextContent(t('settings.common.storedInMeshloom'));
    });

    it('a slow first read never overwrites what an action answered', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      let finishRead: (value: { state: 'confirmed' }) => void = () => undefined;
      vi.spyOn(api, 'getRemoteAlertEmail').mockReturnValue(
        new Promise((resolve) => {
          finishRead = resolve;
        })
      );
      vi.spyOn(api, 'setRemoteAlertEmail').mockResolvedValue({ state: 'pending' });
      await renderAs('admin');
      fireEvent.change(await field(), { target: { value: 'me@example.org' } });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.alertEmail.save') }));
      const shown = await screen.findByTestId('remote-alert-email-state');
      expect(shown).toHaveTextContent(t('settings.remote.alertEmail.state.pending'));
      await act(async () => finishRead({ state: 'confirmed' }));
      expect(shown).toHaveTextContent(t('settings.remote.alertEmail.state.pending'));
    });

    it('a refused action keeps a first read still running', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      let finishRead: (value: { state: 'confirmed' }) => void = () => undefined;
      vi.spyOn(api, 'getRemoteAlertEmail').mockReturnValue(
        new Promise((resolve) => {
          finishRead = resolve;
        })
      );
      vi.spyOn(api, 'setRemoteAlertEmail').mockRejectedValue(
        new ApiError('x', 400, { code: 'remote_alert_email_invalid', message: 'x' })
      );
      await renderAs('admin');
      fireEvent.change(await field(), { target: { value: 'nope' } });
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.alertEmail.save') }));
      await screen.findByRole('alert');
      await act(async () => finishRead({ state: 'confirmed' }));
      expect(screen.getByTestId('remote-alert-email-state')).toHaveTextContent(
        t('settings.remote.alertEmail.state.confirmed')
      );
      expect(
        screen.getByRole('button', { name: t('settings.remote.alertEmail.remove') })
      ).toBeInTheDocument();
    });

    it('removes a confirmed address', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      vi.spyOn(api, 'getRemoteAlertEmail').mockResolvedValue({ state: 'confirmed' });
      const clear = vi.spyOn(api, 'clearRemoteAlertEmail').mockResolvedValue({ state: 'none' });
      await renderAs('admin');
      fireEvent.click(
        await screen.findByRole('button', { name: t('settings.remote.alertEmail.remove') })
      );
      await waitFor(() => expect(clear).toHaveBeenCalled());
      expect(await screen.findByTestId('remote-alert-email-state')).toHaveTextContent(
        t('settings.remote.alertEmail.state.none')
      );
    });

    it('a failed read shows no state and keeps the field; a refusal shows its copy', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      vi.spyOn(api, 'getRemoteAlertEmail').mockRejectedValue(new ApiError('x', 502));
      vi.spyOn(api, 'setRemoteAlertEmail').mockRejectedValue(
        new ApiError('x', 400, { code: 'remote_alert_email_invalid', message: 'x' })
      );
      await renderAs('admin');
      fireEvent.change(await field(), { target: { value: 'nope' } });
      expect(screen.queryByTestId('remote-alert-email-state')).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: t('settings.remote.alertEmail.save') }));
      expect(await screen.findByRole('alert')).toHaveTextContent(
        t('errors.remote_alert_email_invalid')
      );
      expect(await field()).toHaveValue('nope');
    });

    it('is for administrators, while Community is on, and never reads for others', async () => {
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf());
      const read = vi.spyOn(api, 'getRemoteAlertEmail').mockResolvedValue({ state: 'none' });
      await renderAs('member');
      await screen.findByTestId('remote-badge');
      expect(screen.queryByLabelText(t('settings.remote.alertEmail.label'))).toBeNull();
      expect(read).not.toHaveBeenCalled();
      cleanup();
      vi.spyOn(api, 'getRemote').mockResolvedValue(statusOf({ community_enabled: false }));
      await renderAs('admin');
      await screen.findByTestId('remote-badge');
      expect(screen.queryByLabelText(t('settings.remote.alertEmail.label'))).toBeNull();
    });
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
