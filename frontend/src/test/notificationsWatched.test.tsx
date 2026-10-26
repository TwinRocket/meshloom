import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api } from '../api';
import { NotificationRulesPanel } from '../components/settings/notifications/NotificationRulesPanel';
import {
  newWatched,
  watchedError,
  withWatched,
  withoutWatched,
} from '../components/settings/notifications/watchedShared';
import { useNotificationChannels } from '../components/settings/notifications/useNotificationChannels';
import { WatchedNodeToggle } from '../components/WatchedNodeToggle';
import { resetConversationNotifications } from '../hooks/useConversationNotifications';
import i18n from '../i18n';
import type {
  NotificationChannel,
  NotificationRules,
  NotificationWatchedNode,
} from '../notifications/types';
import type { Contact } from '../types';

const limits = { per_min: null, per_hour: null, per_day: null };

function channel(overrides: Partial<NotificationChannel>): NotificationChannel {
  return {
    id: 'ntfy-1',
    type: 'ntfy',
    preset: null,
    name: 'Phone',
    enabled: true,
    language: 'auto',
    config: {},
    secret_fields: [],
    secret_unreadable: false,
    allow_private_network: false,
    include_message_text: 'public_only',
    verify_tls: true,
    quiet_hours_exempt: false,
    rate_per_min: null,
    rate_per_hour: null,
    rate_per_day: null,
    limits,
    sort_order: 1,
    created_at: 1,
    paused_until: null,
    failure_count: 0,
    deletable: true,
    status: { status: 'connected', last_error: null, last_ok_at: null },
    ...overrides,
  };
}

const PUSH = channel({ id: 'builtin:push', type: 'web_push', name: 'Web Push', deletable: false });
const NTFY = channel({});
const KEY = 'ab'.repeat(32);

const NODE: NotificationWatchedNode = {
  public_key: KEY,
  channels: ['ntfy-1'],
  cooldown_s: 3600,
  silence_s: 86_400,
};

const RULES: NotificationRules = {
  version: 1,
  paused_until: null,
  events: {},
  conversations: {},
  keywords: [],
  watched_nodes: [NODE],
  radio_offline_grace_s: 60,
  mention_whole_word: false,
  push_allow_private_endpoints: false,
  host_timezone: 'UTC',
};

const CONTACTS = [{ public_key: KEY, name: 'Hilltop' }] as unknown as Contact[];

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function mockApi(rules: NotificationRules = RULES) {
  vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH, NTFY]);
  vi.spyOn(api, 'getNotificationTypes').mockResolvedValue([]);
  vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules);
  vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events: [] });
}

function Harness() {
  const state = useNotificationChannels();
  return <NotificationRulesPanel state={state} contacts={CONTACTS} chatChannels={[]} />;
}

beforeEach(async () => {
  resetConversationNotifications();
  await i18n.changeLanguage('en');
});
afterEach(() => {
  vi.restoreAllMocks();
  toastMocks.error.mockClear();
});

describe('watched nodes helpers', () => {
  it('builds an entry with the server defaults, lowercase key and no Web Push', () => {
    expect(newWatched(KEY.toUpperCase(), ['builtin:push', 'ntfy-1'])).toEqual(NODE);
  });

  it('replaces, appends and removes by key, case aside', () => {
    expect(withWatched([NODE], { ...NODE, channels: [] })).toEqual([{ ...NODE, channels: [] }]);
    expect(withWatched([NODE], newWatched('cd'.repeat(32), []))).toHaveLength(2);
    expect(withoutWatched([NODE], KEY.toUpperCase())).toEqual([]);
  });

  it('validates the cooldown (minutes) and the silence (hours)', () => {
    expect(watchedError(60, 24)).toBeNull();
    expect(watchedError(1, 1)).toBeNull();
    expect(watchedError(0, 1)).toBe('cooldown');
    expect(watchedError(-1, 24)).toBe('cooldown');
    expect(watchedError(60 * 24 * 31, 24)).toBe('cooldown');
    expect(watchedError(60, 0)).toBe('silence');
    expect(watchedError(60, 24 * 366)).toBe('silence');
    expect(watchedError(1.5, 24)).toBe('cooldown');
  });
});

describe('WatchedNodeToggle (contact sheet)', () => {
  it('turns the watch on with every channel but Web Push, and off again', async () => {
    mockApi({ ...RULES, watched_nodes: [] });
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<WatchedNodeToggle publicKey={KEY.toUpperCase()} />);
    const box = await screen.findByRole('checkbox', { name: t('contactInfo.watch.toggle') });
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toEqual({ watched_nodes: [NODE] });
    // The answer is the cache: the chips of the channels appear, Web Push is not one of them.
    const chip = await screen.findByRole('button', { name: 'Phone' });
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Web Push' })).toBeNull();

    fireEvent.click(chip);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch.mock.calls[1][0]).toEqual({ watched_nodes: [{ ...NODE, channels: [] }] });

    patch.mockResolvedValue({ ...RULES, watched_nodes: [] });
    fireEvent.click(screen.getByRole('checkbox', { name: t('contactInfo.watch.toggle') }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(3));
    expect(patch.mock.calls[2][0]).toEqual({ watched_nodes: [] });
  });

  it('shows a watched node as on, and blocks a 51st node', async () => {
    mockApi();
    render(<WatchedNodeToggle publicKey={KEY} />);
    expect(
      await screen.findByRole('checkbox', { name: t('contactInfo.watch.toggle') })
    ).toBeChecked();
    expect(screen.getByRole('button', { name: 'Phone' })).toHaveAttribute('aria-pressed', 'true');

    resetConversationNotifications();
    vi.restoreAllMocks();
    const fifty = Array.from({ length: 50 }, (_, i) => ({
      ...NODE,
      public_key: i.toString(16).padStart(64, '0'),
    }));
    mockApi({ ...RULES, watched_nodes: fifty });
    render(<WatchedNodeToggle publicKey={'ee'.repeat(32)} />);
    const full = await screen.findAllByRole('checkbox', { name: t('contactInfo.watch.toggle') });
    expect(full[full.length - 1]).toBeDisabled();
  });

  it('renders in French with typographic apostrophes', async () => {
    await i18n.changeLanguage('fr');
    mockApi();
    render(<WatchedNodeToggle publicKey={KEY} />);
    expect(await screen.findByText('Notifier quand il est entendu')).toBeVisible();
    expect(t('contactInfo.watch.noChannel')).toContain('’');
    expect(t('contactInfo.watch.help')).not.toContain("'");
  });
});

describe('WatchedNodesCard (settings)', () => {
  it('lists the nodes by contact name and removes one', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue({
      ...RULES,
      watched_nodes: [],
    });
    render(<Harness />);
    const card = await screen.findByTestId('watched-nodes');
    expect(within(card).getByText('Hilltop')).toBeVisible();
    expect(within(card).getByText(/every 60 min at most · back after 24 h/)).toBeVisible();
    fireEvent.click(
      within(card).getByRole('button', {
        name: t('settings.notifications.watched.remove', { name: 'Hilltop' }),
      })
    );
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toEqual({ watched_nodes: [] });
  });

  it('edits the channels, the cooldown and the silence threshold', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.notifications.watched.edit', { name: 'Hilltop' }),
      })
    );
    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', {
      name: t('settings.notifications.watched.save'),
    });
    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.watched.silence')), {
      target: { value: '0' },
    });
    expect(save).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.watched.silence')), {
      target: { value: '48' },
    });
    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.watched.cooldown')), {
      target: { value: '5' },
    });
    expect(within(dialog).queryByRole('button', { name: 'Web Push' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Phone' }));
    fireEvent.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toEqual({
      watched_nodes: [{ ...NODE, channels: [], cooldown_s: 300, silence_s: 172_800 }],
    });
  });

  it('says so when the list is empty and when it is full', async () => {
    mockApi({ ...RULES, watched_nodes: [] });
    const { unmount } = render(<Harness />);
    expect(await screen.findByText(t('settings.notifications.watched.empty'))).toBeVisible();
    unmount();
    const fifty = Array.from({ length: 50 }, (_, i) => ({
      ...NODE,
      public_key: i.toString(16).padStart(64, '0'),
    }));
    vi.restoreAllMocks();
    mockApi({ ...RULES, watched_nodes: fifty });
    render(<Harness />);
    expect(
      await screen.findByText(t('settings.notifications.watched.full', { max: 50 }))
    ).toBeVisible();
  });
});

describe('the French copy', () => {
  it('uses typographic apostrophes', async () => {
    await i18n.changeLanguage('fr');
    for (const key of [
      'settings.notifications.watched.help',
      'settings.notifications.watched.dialogHelp',
      'settings.notifications.watched.empty',
    ]) {
      expect(t(key)).not.toContain("'");
    }
    expect(t('settings.notifications.watched.help')).toContain('’');
  });
});
