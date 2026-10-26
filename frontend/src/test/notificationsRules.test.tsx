import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api } from '../api';
import { NotificationRulesPanel } from '../components/settings/notifications/NotificationRulesPanel';
import {
  pauseUntil,
  toggledContactTypes,
  withExceptionState,
} from '../components/settings/notifications/rulesShared';
import { useNotificationChannels } from '../components/settings/notifications/useNotificationChannels';
import i18n from '../i18n';
import type {
  NotificationChannel,
  NotificationEventInfo,
  NotificationRules,
} from '../notifications/types';
import type { Channel, Contact } from '../types';

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
const OFF = channel({ id: 'tg-1', name: 'Telegram', enabled: false });

const EVENTS: NotificationEventInfo[] = [
  { id: 'message.dm', urgent: false, configurable: true, locked_channels: [] },
  { id: 'message.mention', urgent: false, configurable: true, locked_channels: [] },
  { id: 'contact.first_seen', urgent: false, configurable: true, locked_channels: [] },
  { id: 'radio.offline', urgent: true, configurable: true, locked_channels: [] },
  {
    id: 'notify.channel_changed',
    urgent: false,
    configurable: true,
    locked_channels: ['builtin:push'],
  },
  { id: 'notify.test', urgent: false, configurable: false, locked_channels: [] },
];

const RULES: NotificationRules = {
  version: 1,
  paused_until: null,
  events: {
    'message.dm': { channels: ['builtin:push'] },
    'contact.first_seen': { channels: ['builtin:push'] },
    'radio.offline': { channels: ['builtin:push'], always_deliver: true },
    'notify.channel_changed': { channels: ['builtin:push'] },
  },
  conversations: {
    'contact-aa11': { 'ntfy-1': true },
    'channel-bb22': { 'builtin:push': false },
  },
  keywords: [],
  watched_nodes: [],
  radio_offline_grace_s: 60,
  mention_whole_word: false,
  push_allow_private_endpoints: false,
  host_timezone: 'UTC',
};

const CONTACTS = [{ public_key: 'aa11', name: 'Alice' }] as unknown as Contact[];
const CHAT_CHANNELS = [{ key: 'bb22', name: '#ops' }] as unknown as Channel[];

function Harness() {
  const state = useNotificationChannels();
  return <NotificationRulesPanel state={state} contacts={CONTACTS} chatChannels={CHAT_CHANNELS} />;
}

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function mockApi(rules: NotificationRules = RULES) {
  vi.spyOn(api, 'getNotificationChannels').mockResolvedValue([PUSH, NTFY, OFF]);
  vi.spyOn(api, 'getNotificationTypes').mockResolvedValue([]);
  vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rules);
  vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events: EVENTS });
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  toastMocks.error.mockClear();
});

const cell = (event: string, name: string) =>
  screen.getByRole('checkbox', {
    name: t('settings.notifications.matrix.cell', {
      event: t(`settings.notifications.matrix.events.${event}.label`),
      channel: name,
    }),
  });

describe('RulesMatrix', () => {
  it('lists configurable events by group and greys disabled channels', async () => {
    mockApi();
    render(<Harness />);
    const table = await screen.findByRole('table');
    expect(within(table).getByRole('columnheader', { name: /Telegram/ })).toBeInTheDocument();
    expect(
      within(table).getByText(t('settings.notifications.matrix.groups.messages'))
    ).toBeVisible();
    expect(within(table).queryByText(/notify_test/)).toBeNull();
    expect(cell('message_dm', 'Web Push')).toBeChecked();
    expect(cell('message_dm', 'Phone')).not.toBeChecked();
  });

  it('PATCHes the expected diff when a cell is toggled', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue({
      ...RULES,
      events: { ...RULES.events, 'message.dm': { channels: ['builtin:push', 'ntfy-1'] } },
    });
    render(<Harness />);
    await screen.findByRole('table');
    fireEvent.click(cell('message_dm', 'Phone'));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        events: { 'message.dm': { channels: ['builtin:push', 'ntfy-1'] } },
      })
    );
    await waitFor(() => expect(cell('message_dm', 'Phone')).toBeChecked());
  });

  it('scrolls to the quiet hours card from the "always deliver" tooltip link', async () => {
    mockApi();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    render(<Harness />);
    await screen.findByRole('table');
    const target = document.getElementById('notification-quiet-hours-section');
    expect(target).not.toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.matrix.alwaysDeliverTip') })
    );
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.contexts[0]).toBe(target);
  });

  it('toggles "always deliver" and shows the event default', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByRole('table');
    const always = (event: string) =>
      screen.getByRole('checkbox', {
        name: t('settings.notifications.matrix.alwaysDeliverFor', {
          event: t(`settings.notifications.matrix.events.${event}.label`),
        }),
      });
    expect(
      screen.getByRole('checkbox', {
        name: t('settings.notifications.matrix.alwaysDeliverUrgent', {
          event: t('settings.notifications.matrix.events.radio_offline.label'),
        }),
      })
    ).toBeDisabled();
    expect(always('message_dm')).not.toBeChecked();
    fireEvent.click(always('message_dm'));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ events: { 'message.dm': { always_deliver: true } } })
    );
  });

  it('locks builtin:push on the channel-changed row', async () => {
    mockApi();
    render(<Harness />);
    await screen.findByRole('table');
    const locked = cell('notify_channel_changed', 'Web Push');
    expect(locked).toBeChecked();
    expect(locked).toBeDisabled();
  });

  it('writes contact types, materialising the other kinds', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByRole('table');
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: t('settings.notifications.matrix.kindCell', {
          kind: t('settings.notifications.matrix.contactTypes.repeater'),
          channel: 'Phone',
        }),
      })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        events: {
          'contact.first_seen': {
            contact_types: {
              companion: ['builtin:push'],
              repeater: ['builtin:push', 'ntfy-1'],
              sensor: ['builtin:push'],
            },
          },
        },
      })
    );
  });

  it('saves the offline grace and the whole-word mention rule', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByRole('table');
    const grace = screen.getByLabelText(t('settings.notifications.radioOfflineGrace'));
    fireEvent.change(grace, { target: { value: '120' } });
    fireEvent.blur(grace);
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ radio_offline_grace_s: 120 }));
    fireEvent.change(grace, { target: { value: '99999' } });
    fireEvent.blur(grace);
    expect(grace).toHaveValue(60);
    fireEvent.click(screen.getByLabelText(t('settings.notifications.matrix.mentionWholeWord')));
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ mention_whole_word: true }));
  });

  it('reports a failed save', async () => {
    mockApi();
    vi.spyOn(api, 'patchNotificationRules').mockRejectedValue(new Error('boom'));
    render(<Harness />);
    await screen.findByRole('table');
    fireEvent.click(cell('message_dm', 'Phone'));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
  });
});

const ALL_IDS = [
  'message.dm',
  'message.channel',
  'message.mention',
  'contact.first_seen',
  'channel.found',
  'telemetry.alert',
  'radio.offline',
  'radio.online',
  'app.started',
  'app.update',
  'notify.destination_changed',
  'notify.channel_changed',
];
const URGENT = ['radio.offline', 'telemetry.alert', 'notify.channel_changed'];

/** What migration 113 writes for a 4.18.x install: `contact_types` plus a union in `channels`. */
const MIGRATED: NotificationRules = {
  ...RULES,
  events: {
    'message.dm': { channels: ['builtin:push', 'ntfy-1'] },
    'message.channel': { channels: ['builtin:push'] },
    'contact.first_seen': {
      channels: ['builtin:push', 'ntfy-1'],
      contact_types: { companion: ['builtin:push'], repeater: ['ntfy-1'], sensor: [] },
    },
    'channel.found': { channels: ['builtin:push'] },
    'radio.offline': { channels: ['builtin:push'], always_deliver: true },
    'radio.online': { channels: ['builtin:push'] },
    'app.started': { channels: ['builtin:push'] },
    'app.update': { channels: ['builtin:push'] },
    'notify.channel_changed': { channels: ['builtin:push'] },
  },
  radio_offline_grace_s: 90,
  mention_whole_word: true,
};

describe('a 4.18.x-shaped payload', () => {
  const events: NotificationEventInfo[] = ALL_IDS.map((id) => ({
    id,
    urgent: URGENT.includes(id),
    configurable: true,
    locked_channels: id === 'notify.channel_changed' ? ['builtin:push'] : [],
  }));

  it('shows every setting and reads contact types from contact_types', async () => {
    mockApi(MIGRATED);
    vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events });
    render(<Harness />);
    await screen.findByRole('table');
    for (const id of ALL_IDS) {
      const key = id.replace(/\./g, '_');
      expect(
        screen.getByText(t(`settings.notifications.matrix.events.${key}.label`))
      ).toBeInTheDocument();
    }
    expect(screen.getByLabelText(t('settings.notifications.radioOfflineGrace'))).toHaveValue(90);
    expect(
      screen.getByLabelText(t('settings.notifications.matrix.mentionWholeWord'))
    ).toBeChecked();
    const kind = (k: string, channel: string) =>
      screen.getByRole('checkbox', {
        name: t('settings.notifications.matrix.kindCell', {
          kind: t(`settings.notifications.matrix.contactTypes.${k}`),
          channel,
        }),
      });
    expect(kind('companion', 'Web Push')).toBeChecked();
    expect(kind('companion', 'Phone')).not.toBeChecked();
    expect(kind('repeater', 'Phone')).toBeChecked();
    expect(kind('sensor', 'Web Push')).not.toBeChecked();
    // The parent row has no box: the engine ignores the event's own channels here.
    expect(
      screen.queryByRole('checkbox', {
        name: t('settings.notifications.matrix.cell', {
          event: t('settings.notifications.matrix.events.contact_first_seen.label'),
          channel: 'Phone',
        }),
      })
    ).toBeNull();
  });

  it('round-trips a contact-type toggle through the answer of the server', async () => {
    mockApi(MIGRATED);
    vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events });
    const next = {
      ...MIGRATED,
      events: {
        ...MIGRATED.events,
        'contact.first_seen': {
          channels: ['builtin:push', 'ntfy-1'],
          contact_types: { companion: ['builtin:push'], repeater: ['ntfy-1'], sensor: ['ntfy-1'] },
        },
      },
    };
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(next);
    render(<Harness />);
    await screen.findByRole('table');
    const sensorPhone = screen.getByRole('checkbox', {
      name: t('settings.notifications.matrix.kindCell', {
        kind: t('settings.notifications.matrix.contactTypes.sensor'),
        channel: 'Phone',
      }),
    });
    fireEvent.click(sensorPhone);
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        events: {
          'contact.first_seen': {
            contact_types: {
              companion: ['builtin:push'],
              repeater: ['ntfy-1'],
              sensor: ['ntfy-1'],
            },
          },
        },
      })
    );
    await waitFor(() => expect(sensorPhone).toBeChecked());
  });

  it('shows urgent events as always delivered, read-only', async () => {
    mockApi(MIGRATED);
    vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events });
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(MIGRATED);
    render(<Harness />);
    await screen.findByRole('table');
    for (const id of URGENT) {
      const box = screen.getByRole('checkbox', {
        name: t('settings.notifications.matrix.alwaysDeliverUrgent', {
          event: t(`settings.notifications.matrix.events.${id.replace('.', '_')}.label`),
        }),
      });
      expect(box).toBeChecked();
      expect(box).toBeDisabled();
    }
    expect(patch).not.toHaveBeenCalled();
  });
});

describe('keyboard', () => {
  it('toggles a cell with the space key', async () => {
    const { default: userEvent } = await import('@testing-library/user-event');
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByRole('table');
    const user = userEvent.setup();
    cell('message_dm', 'Phone').focus();
    await user.keyboard(' ');
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        events: { 'message.dm': { channels: ['builtin:push', 'ntfy-1'] } },
      })
    );
  });
});

describe('load errors', () => {
  it('shows an error with a retry instead of hiding the panel', async () => {
    mockApi();
    const rules = vi.spyOn(api, 'getNotificationRules');
    rules.mockRejectedValueOnce(new Error('down'));
    render(<Harness />);
    const alert = await screen.findByTestId('notification-rules-error');
    expect(alert).toHaveTextContent(t('settings.notifications.rulesLoadFailed'));
    rules.mockResolvedValue(RULES);
    fireEvent.click(screen.getByRole('button', { name: t('settings.notifications.rulesRetry') }));
    expect(await screen.findByRole('table')).toBeInTheDocument();
  });
});

describe('ConversationExceptions', () => {
  it('lets an orphan channel be cleared, dropping orphans from the write', async () => {
    mockApi({
      ...RULES,
      conversations: { 'contact-aa11': { 'ntfy-1': true, 'gone-1': true, 'gone-2': false } },
    });
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByTestId('notification-exceptions');
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.notifications.exceptionOrphan', { name: 'Alice', channel: 'gone-1' }),
      })
    );
    await waitFor(() => expect(put).toHaveBeenCalledWith('contact-aa11', { 'ntfy-1': true }));
  });

  it('shows names from contacts and channels and cycles a chip', async () => {
    mockApi();
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(RULES);
    render(<Harness />);
    const list = await screen.findByTestId('notification-exceptions');
    expect(within(list).getByText('Alice')).toBeInTheDocument();
    expect(within(list).getByText('#ops')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.notifications.exceptionChip', {
          name: 'Alice',
          channel: 'Web Push',
          state: t('settings.notifications.exceptionState.inherit'),
        }),
      })
    );
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith('contact-aa11', { 'ntfy-1': true, 'builtin:push': true })
    );
  });

  it('removes an exception with null', async () => {
    mockApi();
    const put = vi.spyOn(api, 'setNotificationConversationRule').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByTestId('notification-exceptions');
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.notifications.removeOverride', { name: 'Alice' }),
      })
    );
    await waitFor(() => expect(put).toHaveBeenCalledWith('contact-aa11', null));
  });

  it('shows an empty state', async () => {
    mockApi({ ...RULES, conversations: {} });
    render(<Harness />);
    expect(
      await screen.findByText(t('settings.notifications.exceptionsEmpty'))
    ).toBeInTheDocument();
  });
});

describe('QuietHoursControl', () => {
  it('writes the quiet_hours fields one at a time and shows the host zone', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    const panel = await screen.findByTestId('notification-quiet-hours');
    expect(panel).toHaveTextContent('UTC');
    fireEvent.click(screen.getByLabelText(t('settings.notifications.quiet.enabled')));
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ quiet_hours: { enabled: true } }));
    const start = screen.getByLabelText(t('settings.notifications.quiet.start'));
    expect(start).toHaveValue('22:00');
    fireEvent.change(start, { target: { value: '23:30' } });
    fireEvent.blur(start);
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ quiet_hours: { start: '23:30' } }));
    fireEvent.click(within(panel).getByRole('button', { name: t('settings.updates.weekday.6') }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ quiet_hours: { weekdays: [0, 1, 2, 3, 4, 5] } })
    );
    fireEvent.click(screen.getByLabelText(t('settings.notifications.quiet.digest')));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({ quiet_hours: { digest_at_end: false } })
    );
  });

  it('shows the stored window and snaps an emptied time back', async () => {
    mockApi({
      ...RULES,
      quiet_hours: {
        enabled: true,
        start: '21:00',
        end: '06:30',
        weekdays: [5, 6],
        digest_at_end: true,
      },
    });
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    const end = await screen.findByLabelText(t('settings.notifications.quiet.end'));
    expect(end).toHaveValue('06:30');
    expect(screen.getByLabelText(t('settings.notifications.quiet.enabled'))).toBeChecked();
    fireEvent.change(end, { target: { value: '' } });
    fireEvent.blur(end);
    expect(end).toHaveValue('06:30');
    expect(patch).not.toHaveBeenCalled();
  });
});

describe('GlobalPauseControl', () => {
  it('writes paused_until for 1 h, 8 h and tomorrow, and resumes with null', async () => {
    mockApi();
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    await screen.findByRole('table');
    const nowS = Math.floor(Date.now() / 1000);
    const near = (n: number) => expect.closeTo(n, -1);
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.pause.choice_1h') })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith({ paused_until: near(nowS + 3600) })
    );
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.pause.choice_8h') })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith({ paused_until: near(nowS + 8 * 3600) })
    );
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.pause.choice_tomorrow') })
    );
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(3));
    const tomorrow = (patch.mock.calls[2][0] as { paused_until: number }).paused_until;
    expect(new Date(tomorrow * 1000).getHours()).toBe(8);
    expect(tomorrow).toBeGreaterThan(nowS);
  });

  it('shows the remaining time and resumes', async () => {
    mockApi({ ...RULES, paused_until: Math.floor(Date.now() / 1000) + 90 * 60 });
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);
    render(<Harness />);
    const status = await screen.findByText(/1 h 30 min/);
    expect(status).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: t('settings.notifications.pause.off') }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ paused_until: null }));
  });
});

describe('rulesShared', () => {
  it('computes tomorrow 8:00 local', () => {
    const now = new Date(2026, 9, 19, 23, 30).getTime();
    const d = new Date(pauseUntil('tomorrow', now) * 1000);
    expect([d.getDate(), d.getHours(), d.getMinutes()]).toEqual([20, 8, 0]);
  });
  it('clears an exception when its last channel returns to inherit', () => {
    expect(withExceptionState({ a: true }, 'a', 'inherit')).toBeNull();
  });
  it('keeps unset kinds on the event channels', () => {
    expect(toggledContactTypes({ channels: ['x'] }, 'sensor', 'y', true)).toEqual({
      companion: ['x'],
      repeater: ['x'],
      sensor: ['x', 'y'],
    });
  });
});
