import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));
vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: false,
    allSubscriptions: [],
    preferences: null,
    loading: false,
    subscribe: vi.fn(),
    currentSubscriptionId: null,
    patchPreferences: vi.fn(),
    deleteSubscription: vi.fn(),
    testPush: vi.fn(),
    refreshSubscriptions: vi.fn(),
  }),
}));

import { api } from '../api';
import { NotificationStatusProvider } from '../contexts/NotificationStatusContext';
import { NotificationChannelsPanel } from '../components/settings/notifications/NotificationChannelsPanel';
import { useNotificationChannels } from '../components/settings/notifications/useNotificationChannels';
import i18n from '../i18n';
import type {
  NotificationChannel,
  NotificationEventInfo,
  NotificationRules,
  NotificationTypeInfo,
} from '../notifications/types';

const limits = { per_min: null, per_hour: null, per_day: null };

function typeInfo(overrides: Partial<NotificationTypeInfo>): NotificationTypeInfo {
  return {
    type: 'x',
    schema: { properties: {} },
    cloud: false,
    singleton: false,
    preset: false,
    default_limits: limits,
    quiet_hours_exempt_default: false,
    include_message_text_default: 'always',
    ...overrides,
  };
}

const NTFY_TYPE: NotificationTypeInfo = typeInfo({
  type: 'ntfy',
  cloud: true,
  default_limits: { per_min: 6, per_hour: 60, per_day: null },
  schema: { properties: { topic: { type: 'string', title: 'Topic' } }, required: ['topic'] },
});
const TYPES: NotificationTypeInfo[] = [typeInfo({ type: 'web_push', singleton: true }), NTFY_TYPE];

function channel(overrides: Partial<NotificationChannel>): NotificationChannel {
  return {
    id: 'ntfy-aaaaaaaa',
    type: 'ntfy',
    preset: null,
    name: 'Phone',
    enabled: true,
    language: 'auto',
    config: { topic: 'meshloom' },
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

const PUSH = channel({
  id: 'builtin:push',
  type: 'web_push',
  name: 'Web Push',
  config: {},
  deletable: false,
});

const EVENTS: NotificationEventInfo[] = [
  'message.dm',
  'message.mention',
  'radio.offline',
  'radio.online',
  'telemetry.alert',
  'notify.channel_changed',
  'app.started',
].map((id) => ({ id, urgent: false, configurable: true, locked_channels: [] }));

const RULES: NotificationRules = {
  version: 1,
  paused_until: null,
  events: {
    'message.dm': { channels: ['builtin:push'] },
    'radio.offline': { channels: ['builtin:push'] },
    'notify.channel_changed': { channels: ['builtin:push'] },
  },
  conversations: {},
  keywords: [],
  watched_nodes: [],
  radio_offline_grace_s: 60,
  mention_whole_word: false,
  push_allow_private_endpoints: false,
  host_timezone: 'UTC',
};

function Harness({ capped = false }: { capped?: boolean }) {
  const state = useNotificationChannels();
  return (
    <NotificationStatusProvider statuses={undefined} capped={capped}>
      <NotificationChannelsPanel state={state} appSettings={null} />
    </NotificationStatusProvider>
  );
}

function mockApi(channels: NotificationChannel[] = [PUSH, channel({})]) {
  vi.spyOn(api, 'getNotificationChannels').mockResolvedValue(channels);
  vi.spyOn(api, 'getNotificationTypes').mockResolvedValue(TYPES);
  vi.spyOn(api, 'getNotificationRules').mockResolvedValue(RULES);
  vi.spyOn(api, 'getNotificationEvents').mockResolvedValue({ contract_version: 1, events: EVENTS });
}

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterEach(() => {
  vi.restoreAllMocks();
  toastMocks.error.mockClear();
  toastMocks.success.mockClear();
});

const NOW = Math.floor(Date.now() / 1000);
const DELIVERIES = {
  deliveries: [
    {
      id: 9,
      channel_id: 'ntfy-aaaaaaaa',
      event: 'message.dm',
      dedup_key: 'a',
      status: 'failed',
      error: 'auth_failed',
      attempts: 3,
      created_at: NOW - 60,
    },
    {
      id: 8,
      channel_id: 'ntfy-aaaaaaaa',
      event: 'message.dm',
      dedup_key: 'b',
      status: 'suppressed_quiet',
      error: null,
      attempts: 1,
      created_at: NOW - 120,
    },
    {
      id: 7,
      channel_id: 'ntfy-aaaaaaaa',
      event: 'message.mention',
      dedup_key: 'c',
      status: 'suppressed_quiet',
      error: null,
      attempts: 1,
      created_at: NOW - 180,
    },
    {
      id: 6,
      channel_id: 'ntfy-aaaaaaaa',
      event: 'message.dm',
      dedup_key: 'd',
      status: 'aggregated',
      error: null,
      attempts: 1,
      created_at: NOW - 240,
    },
    {
      id: 5,
      channel_id: 'ntfy-aaaaaaaa',
      event: 'message.dm',
      dedup_key: 'e',
      status: 'aggregated',
      error: null,
      attempts: 1,
      created_at: NOW - 3 * 86400,
    },
  ],
  counters: [
    { channel_id: 'ntfy-aaaaaaaa', hour: 1, sent: 4 },
    { channel_id: 'ntfy-aaaaaaaa', hour: 2, sent: 3 },
  ],
};

describe('delivery log', () => {
  it('counts held and grouped notifications of the last 24 h on the card', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockImplementation(async ({ channel } = {}) =>
      channel === 'ntfy-aaaaaaaa' ? DELIVERIES : { deliveries: [], counters: [] }
    );
    render(<Harness />);
    const counters = await screen.findByTestId('channel-counters-ntfy-aaaaaaaa');
    expect(counters).toHaveTextContent(t('settings.notifications.log.suppressed', { count: 2 }));
    expect(counters).toHaveTextContent(t('settings.notifications.log.grouped', { count: 1 }));
    expect(screen.queryByTestId('channel-counters-builtin:push')).not.toBeInTheDocument();
  });

  it('opens the drawer with 50 rows, status badges, the scrubbed error and the 24 h total', async () => {
    mockApi();
    const list = vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue(DELIVERIES);
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.log.open') })
    );
    const log = await screen.findByTestId('delivery-log');
    await waitFor(() =>
      expect(screen.getByTestId('delivery-log-sent')).toHaveTextContent(
        t('settings.notifications.log.sent24h', { count: 7 })
      )
    );
    expect(list).toHaveBeenCalledWith({ channel: 'ntfy-aaaaaaaa', limit: 50 });
    expect(screen.getByTestId('delivery-status-9')).toHaveTextContent(
      t('settings.notifications.log.status.failed')
    );
    expect(screen.getByTestId('delivery-status-8')).toHaveTextContent(
      t('settings.notifications.log.status.suppressed_quiet')
    );
    expect(screen.getByTestId('delivery-status-6')).toHaveTextContent(
      t('settings.notifications.log.status.aggregated')
    );
    expect(screen.getByTestId('delivery-row-9')).toHaveTextContent(t('errors.auth_failed'));

    const calls = list.mock.calls.length;
    fireEvent.click(
      within(log).getByRole('button', { name: t('settings.notifications.log.refresh') })
    );
    await waitFor(() => expect(list.mock.calls.length).toBe(calls + 1));
  });

  it('takes the card counters from the exact server totals, not from the rows', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockImplementation(async ({ channel } = {}) =>
      channel === 'ntfy-aaaaaaaa'
        ? {
            deliveries: [],
            counters: [],
            totals: { sent: 9, suppressed_quiet: 312, aggregated: 41 },
          }
        : { deliveries: [], counters: [] }
    );
    render(<Harness />);
    const counters = await screen.findByTestId('channel-counters-ntfy-aaaaaaaa');
    expect(counters).toHaveTextContent(t('settings.notifications.log.suppressed', { count: 312 }));
    expect(counters).toHaveTextContent(t('settings.notifications.log.grouped', { count: 41 }));
  });

  it('falls back to a generic translated text for an unknown error code', async () => {
    mockApi();
    const row = { ...DELIVERIES.deliveries[0], id: 20, error: 'brand_new_code' };
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue({
      ...DELIVERIES,
      deliveries: [row],
    });
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.log.open') })
    );
    const line = await screen.findByTestId('delivery-row-20');
    expect(line).toHaveTextContent(
      t('settings.notifications.log.errorGeneric', { code: 'brand_new_code' })
    );
    expect(line).toHaveTextContent('Delivery failed (brand_new_code)');
    await i18n.changeLanguage('fr');
    expect(t('settings.notifications.log.errorGeneric', { code: 'x' })).toContain('(x)');
    expect(t('settings.notifications.log.errorGeneric', { code: 'x' })).not.toContain('Delivery');
  });

  it('shows the empty state', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue({ deliveries: [], counters: [] });
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.log.open') })
    );
    expect(await screen.findByText(t('settings.notifications.log.empty'))).toBeInTheDocument();
  });
});

describe('delivery log event labels', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

  /** Every name the dispatcher can write in the journal: the catalogue plus the summaries. */
  function backendEventNames(): string[] {
    const events = read('../../../app/notifications/events.py');
    const block = /EVENT_IDS[^=]*=\s*\(([\s\S]*?)\n\)/.exec(events)?.[1] ?? '';
    const ids = [...block.matchAll(/"([a-z_.]+)"/g)].map((m) => m[1]);
    const dispatcher = read('../../../app/notifications/dispatcher.py');
    const quiet = read('../../../app/notifications/quiet_hours.py');
    const extra = [
      /^GROUPED_EVENT = "([a-z_]+)"/m.exec(dispatcher)?.[1],
      /^FIRST_SEEN_GROUPED_EVENT = "([a-z_]+)"/m.exec(dispatcher)?.[1],
      /^DIGEST_EVENT = "([a-z_]+)"/m.exec(quiet)?.[1],
    ];
    return [...ids, ...extra].filter((id): id is string => Boolean(id));
  }

  it('has an English and a French label for every event the backend can log', async () => {
    const names = backendEventNames();
    expect(names.length).toBeGreaterThanOrEqual(16);
    for (const lng of ['en', 'fr']) {
      await i18n.changeLanguage(lng);
      for (const name of names) {
        const key = `settings.notifications.log.events.${name.replace(/\./g, '_')}`;
        expect(i18n.exists(key), `${lng} ${key}`).toBe(true);
      }
    }
  });

  it('shows the translated name in the drawer', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue(DELIVERIES);
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.log.open') })
    );
    const row = await screen.findByTestId('delivery-row-7');
    expect(row).toHaveTextContent('Mention');
    expect(row).not.toHaveTextContent('message.mention');
  });
});
