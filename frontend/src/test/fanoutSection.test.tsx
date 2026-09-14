import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { SettingsFanoutSection } from '../components/settings/SettingsFanoutSection';
import type { HealthStatus, FanoutConfig } from '../types';
import i18n from '../i18n';
import fanoutEn from '../i18n/locales/slices/d.en.json';
import fanoutFr from '../i18n/locales/slices/d.fr.json';

i18n.addResourceBundle('en', 'translation', fanoutEn, true, true);
i18n.addResourceBundle('fr', 'translation', fanoutFr, true, true);

function tf(key: string, options?: Record<string, unknown>) {
  return i18n.t(`settings.fanout.${key}`, options);
}

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getFanoutConfigs: vi.fn(),
    createFanoutConfig: vi.fn(),
    updateFanoutConfig: vi.fn(),
    deleteFanoutConfig: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    getSettings: vi.fn(),
    getRadioConfig: vi.fn(),
  },
}));

// Suppress BotCodeEditor lazy load in tests
vi.mock('../components/BotCodeEditor', () => ({
  BotCodeEditor: () => <textarea data-testid="bot-code-editor" />,
}));

import { api } from '../api';

const mockedApi = vi.mocked(api);

const baseHealth: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  radio_initializing: false,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

const webhookConfig: FanoutConfig = {
  id: 'wh-1',
  type: 'webhook',
  name: 'Test Hook',
  enabled: true,
  config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
  scope: { messages: 'all', raw_packets: 'none' },
  sort_order: 0,
  created_at: 1000,
};

function renderSection(overrides?: { health?: HealthStatus }) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={vi.fn(async () => {})}
    />
  );
}

function renderSectionWithRefresh(
  onHealthRefresh: () => Promise<void>,
  overrides?: { health?: HealthStatus }
) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={onHealthRefresh}
    />
  );
}

function startsWithAccessibleName(name: string) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped}(?:\\s|$)`);
}

async function openCreateIntegrationDialog() {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: tf('list.addIntegration') })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: tf('list.addIntegration') }));
  return screen.findByRole('dialog', { name: tf('create.title') });
}

function selectCreateIntegration(name: string) {
  const dialog = screen.getByRole('dialog', { name: tf('create.title') });
  fireEvent.click(within(dialog).getByRole('button', { name: startsWithAccessibleName(name) }));
}

function confirmCreateIntegration() {
  const dialog = screen.getByRole('dialog', { name: tf('create.title') });
  fireEvent.click(within(dialog).getByRole('button', { name: tf('create.create') }));
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockedApi.getFanoutConfigs.mockResolvedValue([]);
  mockedApi.getChannels.mockResolvedValue([]);
  mockedApi.getContacts.mockResolvedValue([]);
  mockedApi.getSettings.mockResolvedValue({
    ui_preferences: { nav_rail: [], theme: '' },
    max_radio_contacts: 200,
    auto_decrypt_dm_on_advert: true,
    last_message_times: {},
    advert_interval: 0,
    last_advert_time: 0,
    flood_scope: '',
    known_regions: [],
    blocked_keys: [],
    blocked_names: [],
    discovery_blocked_types: [],
    tracked_telemetry_repeaters: [],
    tracked_telemetry_contacts: [],
    auto_resend_channel: false,
    telemetry_interval_hours: 8,
    telemetry_routed_hourly: false,
  });
  mockedApi.getRadioConfig.mockResolvedValue({
    public_key: 'aa'.repeat(32),
    name: 'TestNode',
    lat: 0,
    lon: 0,
    tx_power: 17,
    max_tx_power: 22,
    radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
    path_hash_mode: 0,
    path_hash_mode_supported: false,
  });
});

describe('SettingsFanoutSection', () => {
  it('shows add integration dialog with all integration types', async () => {
    renderSection();
    const dialog = await openCreateIntegrationDialog();

    const optionButtons = within(dialog)
      .getAllByRole('button')
      .filter((button) => button.hasAttribute('aria-pressed'));
    expect(optionButtons).toHaveLength(11);
    expect(within(dialog).getByRole('button', { name: tf('create.close') })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: tf('create.create') })).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.mqtt_private.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.mqtt_community_meshrank.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.mqtt_community_letsmesh_us.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.mqtt_community_letsmesh_eu.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.mqtt_community.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.webhook.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.apprise.label')),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName(tf('create.sqs.label')) })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName(tf('create.bot.label')) })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName(tf('create.map_upload.label')),
      })
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { level: 3 })).toBeInTheDocument();

    const genericCommunityIndex = optionButtons.findIndex((button) =>
      button.textContent?.startsWith(tf('create.mqtt_community.label'))
    );
    const meshRankIndex = optionButtons.findIndex((button) =>
      button.textContent?.startsWith(tf('create.mqtt_community_meshrank.label'))
    );
    expect(genericCommunityIndex).toBeGreaterThan(-1);
    expect(meshRankIndex).toBeGreaterThan(-1);
    expect(genericCommunityIndex).toBeLessThan(meshRankIndex);
  });

  it('shows bot option in add integration dialog when bots are enabled', async () => {
    renderSection();
    const dialog = await openCreateIntegrationDialog();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName(tf('create.bot.label')) })
    ).toBeInTheDocument();
  });

  it('shows bots disabled banner when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    await waitFor(() => {
      expect(screen.getByText(tf('list.botsDisabled'))).toBeInTheDocument();
    });
  });

  it('shows restart-scoped bots disabled messaging when disabled until restart', async () => {
    renderSection({
      health: { ...baseHealth, bots_disabled: true, bots_disabled_source: 'until_restart' },
    });
    await waitFor(() => {
      expect(screen.getByText(tf('list.botsDisabledUntilRestart'))).toBeInTheDocument();
    });
  });

  it('hides bot option from add integration dialog when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    const dialog = await openCreateIntegrationDialog();
    expect(
      within(dialog).queryByRole('button', {
        name: startsWithAccessibleName(tf('create.bot.label')),
      })
    ).not.toBeInTheDocument();
  });

  it('lists existing configs after load', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });
  });

  it('shows an error info button and dialog when the integration has a retained error', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection({
      health: {
        ...baseHealth,
        fanout_statuses: {
          'wh-1': {
            name: 'Test Hook',
            type: 'webhook',
            status: 'error',
            last_error: 'HTTP 500',
          },
        },
      },
    });

    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole('button', { name: tf('list.viewErrorAria', { name: 'Test Hook' }) })
    );

    expect(
      screen.getByRole('dialog', { name: tf('list.errorTitle', { name: 'Test Hook' }) })
    ).toBeInTheDocument();
    expect(screen.getByText('HTTP 500')).toBeInTheDocument();
  });

  it('does not show an error info button when the integration has no retained error', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection({
      health: {
        ...baseHealth,
        fanout_statuses: {
          'wh-1': {
            name: 'Test Hook',
            type: 'webhook',
            status: 'connected',
          },
        },
      },
    });

    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    expect(
      screen.queryByRole('button', { name: tf('list.viewErrorAria', { name: 'Test Hook' }) })
    ).not.toBeInTheDocument();
  });

  it('navigates to edit view when clicking edit', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const editBtn = screen.getByRole('button', { name: tf('list.edit') });
    fireEvent.click(editBtn);

    await waitFor(() => {
      expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument();
    });
  });

  it('save as enabled returns to list even if health refresh fails', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: true });
    const failingRefresh = vi.fn(async () => {
      throw new Error('refresh failed');
    });

    renderSectionWithRefresh(failingRefresh);
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() => expect(screen.queryByText(tf('list.backToList'))).not.toBeInTheDocument());
    expect(screen.getByText('Test Hook')).toBeInTheDocument();
  });

  it('calls toggle enabled on checkbox click', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: false });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { enabled: false });
    });
  });

  it('webhook with persisted "none" scope renders "All messages" selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'none', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    // "none" is not a valid mode without raw packets — should fall back to "all"
    const allRadio = screen.getByLabelText(tf('scope.allMessages'));
    expect(allRadio).toBeChecked();
  });

  it('does not show "No messages" scope option for webhook', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'all', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByText(tf('scope.allMessages'))).toBeInTheDocument();
    expect(screen.queryByText(tf('scope.noMessages'))).not.toBeInTheDocument();
  });

  it('shows empty scope warning when "only" mode has nothing selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: { channels: [], contacts: [] }, raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByText(tf('scope.emptyWarning'))).toBeInTheDocument();
  });

  it('shows warning for private MQTT when both scope axes are off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByText(tf('scope.emptyWarning'))).toBeInTheDocument();
  });

  it('private MQTT shows raw packets toggle and No messages option', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByText(tf('scope.forwardRaw'))).toBeInTheDocument();
    expect(screen.getByText(tf('scope.noMessages'))).toBeInTheDocument();
  });

  it('private MQTT hides warning when raw packets enabled but messages off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.queryByText(tf('scope.emptyWarning'))).not.toBeInTheDocument();
  });

  it('navigates to create view when clicking add button', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.webhook.label'));
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument();
      expect(screen.getByLabelText(tf('list.name'))).toHaveValue(
        tf('defaultNameCounted', { label: tf('types.webhook'), n: 1 })
      );
      // Should show the URL input for webhook type
      expect(screen.getByLabelText(tf('webhook.url'))).toBeInTheDocument();
    });

    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('new SQS draft shows queue url fields and sensible defaults', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.sqs.label'));
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument();
      expect(screen.getByLabelText(tf('list.name'))).toHaveValue(
        tf('defaultNameCounted', { label: tf('types.sqs'), n: 1 })
      );
      expect(screen.getByLabelText(tf('sqs.queueUrl'))).toBeInTheDocument();
      expect(screen.getByText(tf('scope.forwardRaw'))).toBeInTheDocument();
    });
  });

  it('backing out of a new draft does not create an integration', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.webhook.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.click(screen.getByText(tf('list.backToList')));

    expect(window.confirm).toHaveBeenCalledWith(tf('confirm.leaveUnsaved'));
    await waitFor(() => expect(screen.queryByText(tf('list.backToList'))).not.toBeInTheDocument());
    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('back to list does not ask for confirmation when an existing integration is unchanged', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.click(screen.getByText(tf('list.backToList')));

    expect(window.confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText(tf('list.backToList'))).not.toBeInTheDocument());
  });

  it('back to list asks for confirmation after editing an existing integration', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(tf('webhook.url')), {
      target: { value: 'https://example.com/new' },
    });
    fireEvent.click(screen.getByText(tf('list.backToList')));

    expect(window.confirm).toHaveBeenCalledWith(tf('confirm.leaveUnsaved'));
    await waitFor(() => expect(screen.queryByText(tf('list.backToList'))).not.toBeInTheDocument());
  });

  it('back to list stays on the edit screen when confirmation is cancelled after edits', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(tf('webhook.url')), {
      target: { value: 'https://example.com/new' },
    });
    fireEvent.click(screen.getByText(tf('list.backToList')));

    expect(window.confirm).toHaveBeenCalledWith(tf('confirm.leaveUnsaved'));
    expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument();
  });

  it('saving a new draft creates the integration on demand', async () => {
    const createdWebhook: FanoutConfig = {
      id: 'wh-new',
      type: 'webhook',
      name: 'Webhook #1',
      enabled: false,
      config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdWebhook);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdWebhook]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.webhook.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveDisabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'webhook',
        name: tf('defaultNameCounted', { label: tf('types.webhook'), n: 1 }),
        config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: false,
      })
    );
  });

  it('creates Apprise with outgoing forwarding disabled by default', async () => {
    const createdApprise: FanoutConfig = {
      id: 'ap-new',
      type: 'apprise',
      name: 'Apprise #1',
      enabled: true,
      config: {
        urls: '',
        preserve_identity: true,
        include_outgoing: false,
        markdown_format: true,
        body_format_dm: '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]',
        body_format_channel:
          '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]',
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdApprise);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdApprise]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.apprise.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('apprise.includeOutgoing'))).not.toBeChecked();
    expect(screen.getByText(tf('apprise.includeOutgoingHelp'))).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'apprise',
        name: tf('defaultNameCounted', { label: tf('types.apprise'), n: 1 }),
        config: {
          urls: '',
          preserve_identity: true,
          include_outgoing: false,
          markdown_format: true,
          body_format_dm: '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]',
          body_format_channel:
            '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]',
        },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: true,
      })
    );
  });

  it('can enable outgoing forwarding for an existing Apprise integration', async () => {
    const appriseConfig: FanoutConfig = {
      id: 'ap-1',
      type: 'apprise',
      name: 'Apprise Feed',
      enabled: true,
      config: {
        urls: 'discord://abc',
        preserve_identity: true,
        markdown_format: true,
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([appriseConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({
      ...appriseConfig,
      config: { ...appriseConfig.config, include_outgoing: true },
    });

    renderSection();
    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Apprise Feed' }),
    });
    expect(within(group).getByText('Apprise Feed')).toBeInTheDocument();

    fireEvent.click(within(group).getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    const includeOutgoing = screen.getByLabelText(tf('apprise.includeOutgoing'));
    expect(includeOutgoing).not.toBeChecked();
    fireEvent.click(includeOutgoing);
    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('ap-1', {
        name: 'Apprise Feed',
        config: {
          urls: 'discord://abc',
          preserve_identity: true,
          markdown_format: true,
          include_outgoing: true,
        },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: true,
      })
    );
  });

  it('new draft names increment within the integration type', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      webhookConfig,
      {
        ...webhookConfig,
        id: 'wh-2',
        name: 'Another Hook',
      },
    ]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.webhook.label'));
    confirmCreateIntegration();
    await waitFor(() =>
      expect(screen.getByLabelText(tf('list.name'))).toHaveValue(
        tf('defaultNameCounted', { label: tf('types.webhook'), n: 3 })
      )
    );
  });

  it('clicking a list name allows inline rename and saves on blur', async () => {
    const renamedWebhook = { ...webhookConfig, name: 'Renamed Hook' };
    mockedApi.getFanoutConfigs
      .mockResolvedValueOnce([webhookConfig])
      .mockResolvedValueOnce([renamedWebhook]);
    mockedApi.updateFanoutConfig.mockResolvedValue(renamedWebhook);

    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText(tf('list.editNameAria', { name: 'Test Hook' }));
    fireEvent.change(inlineInput, { target: { value: 'Renamed Hook' } });
    fireEvent.blur(inlineInput);

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { name: 'Renamed Hook' })
    );
    await waitFor(() => expect(screen.getByText('Renamed Hook')).toBeInTheDocument());
  });

  it('escape cancels inline rename without saving', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText(tf('list.editNameAria', { name: 'Test Hook' }));
    fireEvent.change(inlineInput, { target: { value: 'Cancelled Hook' } });
    fireEvent.keyDown(inlineInput, { key: 'Escape' });

    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());
    expect(mockedApi.updateFanoutConfig).not.toHaveBeenCalledWith('wh-1', {
      name: 'Cancelled Hook',
    });
  });

  it('community MQTT editor exposes packet topic template', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('community.packetTopicTemplate'))).toHaveValue(
      'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}'
    );
    expect(screen.getByLabelText(tf('community.transport'))).toHaveValue('tcp');
    expect(screen.getByLabelText(tf('community.authentication'))).toHaveValue('token');
    expect(screen.getByLabelText(tf('community.tokenAudience'))).toHaveValue('meshrank.net');
    const letsMeshAuthLead = tf('community.letsMeshAuthHelp')
      .split(/<[^>]+>/)[0]
      .trim();
    expect(
      screen.getByText((_, element) => {
        const content = element?.textContent ?? '';
        return Boolean(
          letsMeshAuthLead &&
          content.includes(letsMeshAuthLead) &&
          Array.from(element?.children ?? []).every(
            (child) => !child.textContent?.includes(letsMeshAuthLead)
          )
        );
      })
    ).toBeInTheDocument();
  });

  it('existing community MQTT config without auth_mode defaults to token in the editor', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-legacy',
      type: 'mqtt_community',
      name: 'Legacy Community MQTT',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        iata: 'LAX',
        email: 'user@example.com',
        token_audience: '',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Legacy Community MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('community.authentication'))).toHaveValue('token');
    expect(screen.getByLabelText(tf('community.tokenAudience'))).toBeInTheDocument();
  });

  it('community MQTT token audience can be cleared back to blank', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    const audienceInput = screen.getByLabelText(tf('community.tokenAudience'));
    fireEvent.change(audienceInput, { target: { value: '' } });

    expect(audienceInput).toHaveValue('');
  });

  it('existing community MQTT defaults can be cleared while editing and normalize on save', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: '',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({
      ...communityConfig,
      enabled: true,
    });

    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    const hostInput = screen.getByLabelText(tf('mqtt.brokerHost')) as HTMLInputElement;
    const portInput = screen.getByLabelText(tf('mqtt.brokerPort')) as HTMLInputElement;
    const topicTemplateInput = screen.getByLabelText(
      tf('community.packetTopicTemplate')
    ) as HTMLInputElement;

    fireEvent.change(hostInput, { target: { value: '' } });
    fireEvent.change(portInput, { target: { value: '' } });
    fireEvent.change(topicTemplateInput, { target: { value: '' } });

    expect(hostInput.value).toBe('');
    expect(portInput.value).toBe('');
    expect(topicTemplateInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('comm-1', {
        name: 'Community Feed',
        config: {
          broker_host: 'mqtt-us-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          iata: 'LAX',
          email: '',
          token_audience: '',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('community MQTT can be configured for no auth', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'meshrank.net',
        broker_port: 8883,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'none',
        iata: 'LAX',
        topic_template: 'meshrank/uplink/ROOM/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: tf('list.edit') }));
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('community.authentication'))).toHaveValue('none');
    expect(screen.queryByLabelText(tf('community.tokenAudience'))).not.toBeInTheDocument();
  });

  it('community MQTT list shows configured packet topic', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'mqtt-us-v1.letsmesh.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();

    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Community Feed' }),
    });
    expect(
      within(group).getByText(
        (_, element) =>
          element?.textContent === tf('list.broker', { summary: 'mqtt-us-v1.letsmesh.net:443' })
      )
    ).toBeInTheDocument();
    expect(within(group).getByText('mesh2mqtt/{IATA}/node/{PUBLIC_KEY}')).toBeInTheDocument();
    expect(screen.queryByText('Region: LAX')).not.toBeInTheDocument();
  });

  it('MeshRank preset pre-fills the broker settings and asks for the topic template', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_community_meshrank.label'));
    confirmCreateIntegration();

    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('list.name'))).toHaveValue('MeshRank');
    expect(screen.getByLabelText(tf('community.packetTopicTemplate'))).toHaveValue('');
    expect(screen.queryByLabelText(tf('mqtt.brokerHost'))).not.toBeInTheDocument();
  });

  it('private MQTT fields can be cleared while editing and normalize defaults on create', async () => {
    const createdConfig: FanoutConfig = {
      id: 'mqtt-private-1',
      type: 'mqtt_private',
      name: 'Private MQTT 1',
      enabled: true,
      config: {
        broker_host: 'broker.local',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
      },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_private.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(tf('mqtt.brokerHost')), {
      target: { value: 'broker.local' },
    });

    const portInput = screen.getByLabelText(tf('mqtt.brokerPort')) as HTMLInputElement;
    const prefixInput = screen.getByLabelText(tf('mqtt.topicPrefix')) as HTMLInputElement;
    fireEvent.change(portInput, { target: { value: '' } });
    fireEvent.change(prefixInput, { target: { value: '' } });

    expect(portInput.value).toBe('');
    expect(prefixInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_private',
        name: tf('defaultNameCounted', { label: tf('types.mqtt_private'), n: 1 }),
        config: {
          broker_host: 'broker.local',
          broker_port: 1883,
          username: '',
          password: '',
          use_tls: false,
          tls_insecure: false,
          topic_prefix: 'meshcore',
        },
        scope: { messages: 'all', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('creates MeshRank preset as a regular mqtt_community config', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-meshrank',
      type: 'mqtt_community',
      name: 'MeshRank',
      enabled: true,
      config: {
        broker_host: 'meshrank.net',
        broker_port: 8883,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'none',
        username: '',
        password: '',
        iata: 'XYZ',
        email: '',
        token_audience: '',
        topic_template: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_community_meshrank.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(tf('community.packetTopicTemplate')), {
      target: {
        value: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
      },
    });
    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'MeshRank',
        config: {
          broker_host: 'meshrank.net',
          broker_port: 8883,
          transport: 'tcp',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'none',
          username: '',
          password: '',
          iata: 'XYZ',
          email: '',
          token_audience: '',
          topic_template: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('shows Home Assistant topic summary with device-key-derived node ids', async () => {
    mockedApi.getContacts.mockResolvedValue([
      {
        public_key: 'bb'.repeat(32),
        name: 'Alice',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        direct_path_updated_at: null,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        last_contacted: null,
        first_seen: null,
        last_read_at: null,
        favorite: false,
      },
      {
        public_key: 'cc'.repeat(32),
        name: 'Repeater One',
        type: 2,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        direct_path_updated_at: null,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        last_contacted: null,
        first_seen: null,
        last_read_at: null,
        favorite: false,
      },
    ]);
    mockedApi.getSettings.mockResolvedValue({
      ui_preferences: { nav_rail: [], theme: '' },
      max_radio_contacts: 200,
      auto_decrypt_dm_on_advert: true,
      last_message_times: {},
      advert_interval: 0,
      last_advert_time: 0,
      flood_scope: '',
      known_regions: [],
      blocked_keys: [],
      blocked_names: [],
      discovery_blocked_types: [],
      tracked_telemetry_repeaters: ['cc'.repeat(32)],
      tracked_telemetry_contacts: [],
      auto_resend_channel: false,
      telemetry_interval_hours: 8,
      telemetry_routed_hourly: false,
    });

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_ha.label'));
    confirmCreateIntegration();

    expect(await screen.findByText(tf('ha.publishedTopicSummary'))).toBeInTheDocument();

    fireEvent.click(await screen.findByLabelText(/Alice/));
    fireEvent.click(await screen.findByLabelText(/Repeater One/));

    await waitFor(() => {
      expect(
        screen.getAllByText(tf('ha.nodeId', { id: 'aaaaaaaaaaaa' })).length
      ).toBeGreaterThanOrEqual(2);
      expect(screen.getByText(tf('ha.nodeId', { id: 'bbbbbbbbbbbb' }))).toBeInTheDocument();
      expect(screen.getByText(tf('ha.nodeId', { id: 'cccccccccccc' }))).toBeInTheDocument();
    });

    expect(screen.getByText('meshcore/aaaaaaaaaaaa/health')).toBeInTheDocument();
    expect(screen.getByText('meshcore/aaaaaaaaaaaa/events/message')).toBeInTheDocument();
    expect(screen.getByText('meshcore/bbbbbbbbbbbb/gps')).toBeInTheDocument();
    expect(screen.getByText('meshcore/cccccccccccc/telemetry')).toBeInTheDocument();
  });

  it('LetsMesh (US) preset pre-fills the expected broker defaults', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-letsmesh-us',
      type: 'mqtt_community',
      name: 'LetsMesh (US)',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        username: '',
        password: '',
        iata: 'LAX',
        email: 'user@example.com',
        token_audience: 'mqtt-us-v1.letsmesh.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_community_letsmesh_us.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('list.name'))).toHaveValue('LetsMesh (US)');
    expect(screen.queryByLabelText(tf('community.authentication'))).not.toBeInTheDocument();
    expect(screen.queryByLabelText(tf('community.packetTopicTemplate'))).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(tf('letsmesh.email')), {
      target: { value: 'user@example.com' },
    });
    fireEvent.change(screen.getByLabelText(tf('community.regionCode')), {
      target: { value: 'lax' },
    });
    fireEvent.click(screen.getByRole('button', { name: tf('list.saveDisabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'LetsMesh (US)',
        config: {
          broker_host: 'mqtt-us-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          username: '',
          password: '',
          iata: 'LAX',
          email: 'user@example.com',
          token_audience: 'mqtt-us-v1.letsmesh.net',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: false,
      })
    );
  });

  it('map upload geofence radius can be cleared while editing and normalizes to zero', async () => {
    const createdConfig: FanoutConfig = {
      id: 'map-1',
      type: 'map_upload',
      name: 'Map Upload 1',
      enabled: true,
      config: {
        api_url: '',
        dry_run: true,
        geofence_enabled: true,
        geofence_radius_km: 0,
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.map_upload.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.click(screen.getByText(tf('map.enableGeofence')));
    const radiusInput = screen.getByLabelText(tf('map.radius')) as HTMLInputElement;

    fireEvent.change(radiusInput, { target: { value: '100' } });
    fireEvent.change(radiusInput, { target: { value: '' } });

    expect(radiusInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'map_upload',
        name: tf('defaultNameCounted', { label: tf('types.map_upload'), n: 1 }),
        config: {
          api_url: '',
          dry_run: true,
          geofence_enabled: true,
          geofence_radius_km: 0,
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('LetsMesh (EU) preset saves the EU broker defaults', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-letsmesh-eu',
      type: 'mqtt_community',
      name: 'LetsMesh (EU)',
      enabled: true,
      config: {
        broker_host: 'mqtt-eu-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        username: '',
        password: '',
        iata: 'AMS',
        email: 'user@example.com',
        token_audience: 'mqtt-eu-v1.letsmesh.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_community_letsmesh_eu.label'));
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(tf('letsmesh.email')), {
      target: { value: 'user@example.com' },
    });
    fireEvent.change(screen.getByLabelText(tf('community.regionCode')), {
      target: { value: 'ams' },
    });
    fireEvent.click(screen.getByRole('button', { name: tf('list.saveEnabled') }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'LetsMesh (EU)',
        config: {
          broker_host: 'mqtt-eu-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          username: '',
          password: '',
          iata: 'AMS',
          email: 'user@example.com',
          token_audience: 'mqtt-eu-v1.letsmesh.net',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('generic Community MQTT entry still opens the full editor', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration(tf('create.mqtt_community.label'));
    confirmCreateIntegration();

    await waitFor(() => expect(screen.getByText(tf('list.backToList'))).toBeInTheDocument());

    expect(screen.getByLabelText(tf('list.name'))).toHaveValue(
      tf('defaultNameCounted', { label: tf('types.mqtt_community'), n: 1 })
    );
    expect(screen.getByLabelText(tf('mqtt.brokerHost'))).toBeInTheDocument();
    expect(screen.getByLabelText(tf('community.authentication'))).toBeInTheDocument();
    expect(screen.getByLabelText(tf('community.packetTopicTemplate'))).toBeInTheDocument();
  });

  it('private MQTT list shows broker and topic summary', async () => {
    const privateConfig: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'Private Broker',
      enabled: true,
      config: { broker_host: 'broker.local', broker_port: 1883, topic_prefix: 'meshcore' },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([privateConfig]);
    renderSection();

    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Private Broker' }),
    });
    expect(
      within(group).getByText(
        (_, element) => element?.textContent === tf('list.broker', { summary: 'broker.local:1883' })
      )
    ).toBeInTheDocument();
    expect(
      within(group).getByText('meshcore/dm:<pubkey>, meshcore/gm:<channel>, meshcore/raw/...')
    ).toBeInTheDocument();
  });

  it('webhook list shows destination URL', async () => {
    const config: FanoutConfig = {
      id: 'wh-1',
      type: 'webhook',
      name: 'Webhook Feed',
      enabled: true,
      config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Webhook Feed' }),
    });
    expect(within(group).getByText('https://example.com/hook')).toBeInTheDocument();
  });

  it('apprise list shows compact target summary', async () => {
    const config: FanoutConfig = {
      id: 'ap-1',
      type: 'apprise',
      name: 'Apprise Feed',
      enabled: true,
      config: {
        urls: 'discord://abc\nmailto://one@example.com\nmailto://two@example.com',
        preserve_identity: true,
        include_path: true,
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Apprise Feed' }),
    });
    expect(
      within(group).getByText(/discord:\/\/\*{8}, mailto:\/\/\*{8}, mailto:\/\/\*{8}/)
    ).toBeInTheDocument();
  });

  it('sqs list shows queue url summary', async () => {
    const config: FanoutConfig = {
      id: 'sqs-1',
      type: 'sqs',
      name: 'Queue Feed',
      enabled: true,
      config: {
        queue_url: 'https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events',
        region_name: 'us-east-1',
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', {
      name: tf('list.integrationAria', { name: 'Queue Feed' }),
    });
    expect(
      within(group).getByText('https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events')
    ).toBeInTheDocument();
  });

  it('groups integrations by type and sorts entries alphabetically within each group', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      {
        ...webhookConfig,
        id: 'wh-b',
        name: 'Zulu Hook',
      },
      {
        ...webhookConfig,
        id: 'wh-a',
        name: 'Alpha Hook',
      },
      {
        id: 'ap-1',
        type: 'apprise',
        name: 'Bravo Alerts',
        enabled: true,
        config: { urls: 'discord://abc', preserve_identity: true, include_path: true },
        scope: { messages: 'all', raw_packets: 'none' },
        sort_order: 0,
        created_at: 1000,
      },
    ]);
    renderSection();

    const webhookGroup = await screen.findByRole('region', {
      name: tf('list.groupAria', { label: tf('types.webhook') }),
    });
    const appriseGroup = screen.getByRole('region', {
      name: tf('list.groupAria', { label: tf('types.apprise') }),
    });

    expect(
      screen.queryByRole('region', {
        name: tf('list.groupAria', { label: tf('types.mqtt_private') }),
      })
    ).not.toBeInTheDocument();
    expect(within(webhookGroup).getByText('Alpha Hook')).toBeInTheDocument();
    expect(within(webhookGroup).getByText('Zulu Hook')).toBeInTheDocument();
    expect(within(appriseGroup).getByText('Bravo Alerts')).toBeInTheDocument();

    const alpha = within(webhookGroup).getByText('Alpha Hook');
    const zulu = within(webhookGroup).getByText('Zulu Hook');
    expect(alpha.compareDocumentPosition(zulu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
