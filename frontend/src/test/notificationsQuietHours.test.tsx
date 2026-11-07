import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

describe('advanced channel settings', () => {
  it('shows the type defaults and sends the limits and quiet_hours_exempt in the PATCH', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue({ deliveries: [], counters: [] });
    const update = vi.spyOn(api, 'updateNotificationChannel').mockResolvedValue(channel({}));
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.channels.edit') })
    );
    const advanced = await screen.findByTestId('channel-advanced');
    expect(advanced).toHaveTextContent(t('settings.notifications.advanced.default', { count: 6 }));
    expect(advanced).toHaveTextContent(t('settings.notifications.advanced.noDefault'));

    fireEvent.change(within(advanced).getByLabelText(t('settings.notifications.advanced.perMin')), {
      target: { value: '3' },
    });
    fireEvent.change(within(advanced).getByLabelText(t('settings.notifications.advanced.perDay')), {
      target: { value: '100' },
    });
    fireEvent.click(
      within(advanced).getByRole('checkbox', {
        name: t('settings.notifications.channels.quietExempt'),
      })
    );
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.channels.save') })
    );
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toBe('ntfy-aaaaaaaa');
    expect(update.mock.calls[0][1]).toMatchObject({
      rate_per_min: 3,
      rate_per_hour: null,
      rate_per_day: 100,
      quiet_hours_exempt: true,
    });
  });

  it('starts from the stored limits and sends null for an emptied field', async () => {
    mockApi([PUSH, channel({ rate_per_hour: 20, quiet_hours_exempt: true })]);
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue({ deliveries: [], counters: [] });
    const update = vi.spyOn(api, 'updateNotificationChannel').mockResolvedValue(channel({}));
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.channels.edit') })
    );
    const advanced = await screen.findByTestId('channel-advanced');
    const hour = within(advanced).getByLabelText(t('settings.notifications.advanced.perHour'));
    expect(hour).toHaveValue(20);
    expect(
      within(advanced).getByRole('checkbox', {
        name: t('settings.notifications.channels.quietExempt'),
      })
    ).toBeChecked();
    fireEvent.change(hour, { target: { value: '' } });
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.channels.save') })
    );
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({
      rate_per_hour: null,
      quiet_hours_exempt: true,
    });
  });
});

describe('capped banner', () => {
  it('shows when the dispatcher reports the global cap, and not otherwise', async () => {
    mockApi();
    vi.spyOn(api, 'getNotificationDeliveries').mockResolvedValue({ deliveries: [], counters: [] });
    const { unmount } = render(<Harness capped />);
    expect(await screen.findByTestId('notifications-capped-banner')).toHaveTextContent(
      t('settings.notifications.cappedBanner')
    );
    unmount();
    render(<Harness />);
    await screen.findByTestId('channel-list');
    expect(screen.queryByTestId('notifications-capped-banner')).not.toBeInTheDocument();
  });
});
