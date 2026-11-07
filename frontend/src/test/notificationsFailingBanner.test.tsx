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

function Harness({ failing = true }: { failing?: boolean }) {
  const state = useNotificationChannels();
  return (
    <NotificationStatusProvider statuses={undefined} capped={false} failing={failing}>
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

describe('failing channels banner (MSHL-130)', () => {
  const FAILING = channel({ id: 'ntfy-failing', name: 'Phone', failure_count: 4 });
  const PAUSED = channel({
    id: 'ntfy-paused',
    name: 'Tablet',
    failure_count: 20,
    paused_until: 2_000_000_000,
    status: { status: 'paused', last_error: 'http_503', last_ok_at: null },
  });
  const UNREADABLE = channel({ id: 'ntfy-secret', name: 'Desk', secret_unreadable: true });
  const HEALTHY = channel({ id: 'ntfy-ok', name: 'Laptop', failure_count: 2 });

  it('lists failing, paused and secret-unreadable channels, with badges', async () => {
    mockApi([PUSH, FAILING, PAUSED, UNREADABLE, HEALTHY]);
    render(<Harness />);
    const banner = await screen.findByTestId('notifications-failing-banner');
    expect(banner).toHaveTextContent(t('settings.notifications.attention.title'));
    expect(within(banner).getByTestId('failing-channel-ntfy-failing')).toHaveTextContent(
      t('settings.notifications.attention.failing', { name: 'Phone', count: 4 })
    );
    expect(within(banner).getByTestId('failing-channel-ntfy-paused')).toHaveTextContent('Tablet');
    expect(within(banner).getByTestId('failing-channel-ntfy-secret')).toHaveTextContent(
      t('settings.notifications.attention.secret_unreadable', { name: 'Desk' })
    );
    // Below the threshold: no row, no badge.
    expect(within(banner).queryByTestId('failing-channel-ntfy-ok')).not.toBeInTheDocument();
    expect(screen.queryByTestId('channel-attention-ntfy-ok')).not.toBeInTheDocument();
    expect(screen.getByTestId('channel-attention-ntfy-paused')).toHaveTextContent(
      t('settings.notifications.attention.badge.paused')
    );
    // A secret the key cannot open is fixed by entering it again, not by Resume.
    expect(within(banner).queryByTestId('failing-resume-ntfy-secret')).not.toBeInTheDocument();
    expect(within(banner).getByTestId('failing-test-ntfy-secret')).toBeInTheDocument();
  });

  it('runs a test send and a resume from the banner', async () => {
    mockApi([PUSH, FAILING, PAUSED]);
    const test = vi.spyOn(api, 'testNotificationChannel').mockResolvedValue({ status: 'ok' });
    const resume = vi
      .spyOn(api, 'resumeNotificationChannel')
      .mockResolvedValue({ ...PAUSED, paused_until: null, failure_count: 0 });
    render(<Harness />);
    fireEvent.click(await screen.findByTestId('failing-test-ntfy-failing'));
    await waitFor(() => expect(test).toHaveBeenCalledWith('ntfy-failing'));
    fireEvent.click(screen.getByTestId('failing-resume-ntfy-paused'));
    await waitFor(() => expect(resume).toHaveBeenCalledWith('ntfy-paused'));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        t('settings.notifications.attention.resumed', { name: 'Tablet' })
      )
    );
  });

  it('is absent when every channel is healthy', async () => {
    mockApi([PUSH, HEALTHY]);
    render(<Harness failing={false} />);
    await screen.findByTestId('channel-list');
    expect(screen.queryByTestId('notifications-failing-banner')).not.toBeInTheDocument();
  });
});
