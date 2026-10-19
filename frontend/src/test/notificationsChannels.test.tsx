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

import { api, ApiError } from '../api';
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

const TYPES: NotificationTypeInfo[] = [
  typeInfo({ type: 'web_push', singleton: true }),
  typeInfo({
    type: 'ntfy',
    cloud: true,
    include_message_text_default: 'public_only',
    schema: {
      properties: {
        topic: { type: 'string', title: 'Topic' },
        token: {
          anyOf: [{ type: 'string', writeOnly: true }, { type: 'null' }],
          writeOnly: true,
          title: 'Token',
        },
      },
      required: ['topic'],
    },
  }),
  // The schema as the server generates it: `label_key` / `help_key` extensions, a hidden flag.
  typeInfo({
    type: 'webhook',
    schema: {
      properties: {
        url: {
          anyOf: [{ type: 'string', writeOnly: true }, { type: 'null' }],
          writeOnly: true,
          label_key: 'notifications.channels.fields.url',
          help_key: 'notifications.channels.help.webhook_url',
        },
        private_network_grandfathered: { type: 'boolean', hidden: true, readOnly: true },
      },
    },
  }),
  typeInfo({
    type: 'telegram',
    preset: true,
    base_type: 'apprise',
    cloud: true,
    include_message_text_default: 'public_only',
    schema: {
      properties: {
        bot_token: { type: 'string', writeOnly: true, title: 'Bot token' },
        chat_id: { type: 'string', title: 'Chat id' },
      },
      required: ['bot_token', 'chat_id'],
    },
  }),
];

function channel(overrides: Partial<NotificationChannel>): NotificationChannel {
  return {
    id: 'ntfy-aaaaaaaa',
    type: 'ntfy',
    preset: null,
    name: 'Phone',
    enabled: true,
    language: 'auto',
    config: { topic: 'meshloom', token: '********' },
    secret_fields: ['token'],
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
  secret_fields: [],
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

function Harness() {
  const state = useNotificationChannels();
  return <NotificationChannelsPanel state={state} appSettings={null} />;
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
  vi.unstubAllGlobals();
  toastMocks.error.mockClear();
  toastMocks.success.mockClear();
});

async function openAdd() {
  render(<Harness />);
  await screen.findByTestId('channel-list');
  fireEvent.click(screen.getByRole('button', { name: t('settings.notifications.channels.add') }));
  return await screen.findByTestId('channel-type-picker');
}

describe('channel list', () => {
  it('renders the cards from the API and flags Web Push over HTTP', async () => {
    vi.stubGlobal('isSecureContext', false);
    mockApi();
    render(<Harness />);
    expect(await screen.findByTestId('channel-card-ntfy-aaaaaaaa')).toHaveTextContent('Phone');
    expect(screen.getByTestId('channel-card-builtin:push')).toBeInTheDocument();
    expect(screen.getByTestId('push-unavailable-http')).toBeInTheDocument();
    expect(screen.getByTestId('channel-status-ntfy-aaaaaaaa')).toHaveTextContent('Working');
  });

  it('explains a 409 legacy_managed answer instead of the raw error', async () => {
    mockApi([PUSH, channel({ id: 'legacy:webhook', type: 'webhook', name: 'Webhook' })]);
    vi.spyOn(api, 'updateNotificationChannel').mockRejectedValue(
      new ApiError('conflict', 409, { code: 'legacy_managed', params: {} })
    );
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-legacy:webhook');
    fireEvent.click(within(card).getByRole('switch'));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    expect(toastMocks.error.mock.calls[0][1].description).toBe(t('errors.legacy_managed'));
    expect(toastMocks.error.mock.calls[0][1].description).toContain('4.18');
  });
});

describe('add dialog', () => {
  it('tests before saving, shows a translated error, then creates with secrets and the Alerts only rows', async () => {
    mockApi();
    const test = vi
      .spyOn(api, 'testNotificationConfig')
      .mockRejectedValueOnce(new ApiError('bad', 502, { code: 'auth_failed', params: {} }))
      .mockResolvedValueOnce({ status: 'sent' });
    const create = vi
      .spyOn(api, 'createNotificationChannel')
      .mockResolvedValue(channel({ id: 'ntfy-bbbbbbbb' }));
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);

    const picker = await openAdd();
    expect(within(picker).queryByText('Web Push')).not.toBeInTheDocument();
    fireEvent.click(within(picker).getByText('ntfy'));

    // The type's fields come from the schema through the shared SchemaForm.
    fireEvent.change(screen.getByLabelText(t('settings.notifications.channels.name')), {
      target: { value: 'My phone' },
    });
    fireEvent.change(screen.getByLabelText(/Topic/), { target: { value: 'meshloom-x' } });
    fireEvent.change(screen.getByLabelText(/Token/), { target: { value: 'tk_secret' } });
    expect(screen.getByTestId('cloud-privacy-note')).toBeInTheDocument();
    expect(screen.getByLabelText(t('settings.notifications.channels.includeText'))).toHaveValue(
      'public_only'
    );

    const submit = t('settings.notifications.channels.testAndContinue');
    fireEvent.click(screen.getByRole('button', { name: submit }));
    expect(await screen.findByTestId('channel-form-error')).toHaveTextContent(
      t('errors.auth_failed')
    );
    expect(create).not.toHaveBeenCalled();
    // After the refusal no secret stays in the page.
    expect(screen.queryByDisplayValue('tk_secret')).not.toBeInTheDocument();

    // Test again (the secret is retyped), then go on to the last step.
    fireEvent.change(screen.getByLabelText(/Token/), { target: { value: 'tk_secret' } });
    fireEvent.click(screen.getByRole('button', { name: submit }));
    await screen.findByText(t('settings.notifications.channels.receiveTitle'));
    expect(test).toHaveBeenCalledTimes(2);
    expect(test.mock.calls[1][0]).toMatchObject({
      type: 'ntfy',
      name: 'My phone',
      config: { topic: 'meshloom-x', token: 'tk_secret' },
      include_message_text: 'public_only',
    });

    // "Like Push" is the default; pick "Alerts only".
    expect(
      screen.getByLabelText(
        new RegExp(`^${t('settings.notifications.channels.receive.like_push')}`)
      )
    ).toBeChecked();
    fireEvent.click(
      screen.getByLabelText(
        new RegExp(`^${t('settings.notifications.channels.receive.alerts_only')}`)
      )
    );
    fireEvent.click(screen.getByRole('button', { name: t('settings.notifications.channels.add') }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({
      type: 'ntfy',
      config: { topic: 'meshloom-x', token: 'tk_secret' },
    });
    const events = patch.mock.calls[0][0].events ?? {};
    expect(Object.keys(events).sort()).toEqual([
      'message.mention',
      'notify.channel_changed',
      'radio.offline',
      'radio.online',
      'telemetry.alert',
    ]);
    expect(events['radio.offline'].channels).toEqual(['builtin:push', 'ntfy-bbbbbbbb']);
    expect(events['message.mention'].channels).toEqual(['ntfy-bbbbbbbb']);
    expect(events['message.dm']).toBeUndefined();
  });

  it('renders the server schema keys (label_key, help_key) and skips hidden fields', async () => {
    mockApi();
    const picker = await openAdd();
    fireEvent.click(within(picker).getByText('Webhook'));
    expect(screen.getByLabelText(/Webhook URL/)).toBeInTheDocument();
    expect(screen.getByText(/receives a JSON POST/)).toBeInTheDocument();
    expect(screen.queryByTestId('schema-form-unsupported')).not.toBeInTheDocument();
    expect(screen.queryByText(/grandfathered/i)).not.toBeInTheDocument();
  });

  it('renders the telegram preset and creates it on the apprise type', async () => {
    mockApi();
    vi.spyOn(api, 'testNotificationConfig').mockResolvedValue({ status: 'sent' });
    const create = vi
      .spyOn(api, 'createNotificationChannel')
      .mockResolvedValue(channel({ id: 'apprise-cccccccc' }));
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(RULES);

    const picker = await openAdd();
    fireEvent.click(within(picker).getByText('Telegram'));
    fireEvent.change(screen.getByLabelText(t('settings.notifications.channels.name')), {
      target: { value: 'Bot' },
    });
    fireEvent.change(screen.getByLabelText(/Bot token/), { target: { value: '123:abc' } });
    fireEvent.change(screen.getByLabelText(/Chat id/), { target: { value: '42' } });
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.notifications.channels.testAndContinue') })
    );
    await screen.findByText(t('settings.notifications.channels.receiveTitle'));
    fireEvent.click(screen.getByRole('button', { name: t('settings.notifications.channels.add') }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({
      type: 'apprise',
      preset: 'telegram',
      config: { bot_token: '123:abc', chat_id: '42' },
    });
    // Like Push: the events Web Push receives.
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(Object.keys(patch.mock.calls[0][0].events ?? {}).sort()).toEqual([
      'message.dm',
      'notify.channel_changed',
      'radio.offline',
    ]);
  });
});

describe('edit dialog', () => {
  it('shows secrets as set, keeps them unless replaced and never puts them in the DOM', async () => {
    mockApi();
    const update = vi
      .spyOn(api, 'updateNotificationChannel')
      .mockResolvedValue(channel({ name: 'Renamed' }));
    render(<Harness />);
    const card = await screen.findByTestId('channel-card-ntfy-aaaaaaaa');
    fireEvent.click(
      within(card).getByRole('button', { name: t('settings.notifications.channels.edit') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(t('schemaForm.secretSet'))).toBeInTheDocument();
    expect(screen.queryByDisplayValue('********')).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(t('settings.notifications.channels.name')), {
      target: { value: 'Renamed' },
    });
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.notifications.channels.save') })
    );
    await waitFor(() => expect(update).toHaveBeenCalled());
    const [id, body] = update.mock.calls[0];
    expect(id).toBe('ntfy-aaaaaaaa');
    expect(body.name).toBe('Renamed');
    // The kept secret is not sent.
    expect(body.config).not.toHaveProperty('token');
    expect(document.body.innerHTML).not.toContain('********');
  });
});
