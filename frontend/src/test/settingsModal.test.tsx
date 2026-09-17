import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../components/LocationPickerModal', () => ({
  LocationPickerModal: ({
    open,
    onApply,
    onOpenChange,
  }: {
    open: boolean;
    onApply: (lat: number, lon: number) => void;
    onOpenChange: (open: boolean) => void;
  }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          onApply(45.764043, 4.835659);
          onOpenChange(false);
        }}
      >
        apply-map-pick
      </button>
    ) : null,
}));

import { SettingsModal } from '../components/SettingsModal';
import i18n from '../i18n';
import type {
  AppSettings,
  AppSettingsUpdate,
  Contact,
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioRegionDiscoveryResponse,
  RadioTransportConfig,
  StatisticsResponse,
} from '../types';
import type { SettingsSection } from '../components/settings/settingsConstants';
import {
  LAST_VIEWED_CONVERSATION_KEY,
  REOPEN_LAST_CONVERSATION_KEY,
} from '../utils/lastViewedConversation';
import { api } from '../api';
import { DISTANCE_UNIT_KEY } from '../utils/distanceUnits';
import { SHOW_PATH_HOP_WIDTH_KEY } from '../utils/pathHopWidthPreference';
import { DEFAULT_LOCALE, LANGUAGE_STORAGE_KEY } from '../utils/languagePreference';
import {
  DEFAULT_FONT_SCALE,
  FONT_SCALE_KEY,
  MAX_FONT_SCALE,
  MIN_FONT_SCALE,
} from '../utils/fontScale';

const baseConfig: RadioConfig = {
  public_key: 'aa'.repeat(32),
  name: 'TestNode',
  lat: 1,
  lon: 2,
  tx_power: 17,
  max_tx_power: 22,
  radio: {
    freq: 910.525,
    bw: 62.5,
    sf: 7,
    cr: 5,
  },
  path_hash_mode: 0,
  path_hash_mode_supported: false,
  advert_location_source: 'current',
  multi_acks_enabled: false,
};

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

const baseSettings: AppSettings = {
  ui_preferences: { nav_rail: [], theme: '' },
  max_radio_contacts: 200,
  auto_decrypt_dm_on_advert: false,
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
};

const baseTransport: RadioTransportConfig = {
  configured: true,
  transport: 'serial',
  serial_port: '/dev/ttyUSB0',
  serial_baudrate: 115200,
  tcp_host: '',
  tcp_port: 5000,
  ble_address: '',
  ble_pin_configured: false,
  bound_public_key: null,
  capabilities: {
    tcp: true,
    serial: true,
    ble: false,
    serial_unavailable_reason: null,
    ble_unavailable_reason: null,
  },
  serial_ports: [{ path: '/dev/ttyUSB0', description: 'USB Serial' }],
};

function renderModal(overrides?: {
  config?: RadioConfig | null;
  appSettings?: AppSettings;
  health?: HealthStatus;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  onRefreshAppSettings?: () => Promise<void>;
  onSave?: (update: RadioConfigUpdate) => Promise<void>;
  onClose?: () => void;
  onSetPrivateKey?: (key: string) => Promise<void>;
  onReboot?: () => Promise<void>;
  onDisconnect?: () => Promise<void>;
  onReconnect?: () => Promise<void>;
  onAdvertise?: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery?: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget?: RadioDiscoveryTarget | null;
  onDiscoverMesh?: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery?: RadioRegionDiscoveryResponse | null;
  contacts?: Contact[];
  trackedTelemetryRepeaters?: string[];
  open?: boolean;
  pageMode?: boolean;
  externalSidebarNav?: boolean;
  desktopSection?: SettingsSection;
  mobile?: boolean;
}) {
  setMatchMedia(overrides?.mobile ?? false);

  const onSaveAppSettings = overrides?.onSaveAppSettings ?? vi.fn(async () => {});
  const onRefreshAppSettings = overrides?.onRefreshAppSettings ?? vi.fn(async () => {});
  const onSave = overrides?.onSave ?? vi.fn(async (_update: RadioConfigUpdate) => {});
  const onClose = overrides?.onClose ?? vi.fn();
  const onSetPrivateKey = overrides?.onSetPrivateKey ?? vi.fn(async () => {});
  const onReboot = overrides?.onReboot ?? vi.fn(async () => {});
  const onDisconnect = overrides?.onDisconnect ?? vi.fn(async () => {});
  const onReconnect = overrides?.onReconnect ?? vi.fn(async () => {});
  const onAdvertise = overrides?.onAdvertise ?? vi.fn(async (_mode: RadioAdvertMode) => {});
  const onDiscoverMesh = overrides?.onDiscoverMesh ?? vi.fn(async () => {});
  const onDiscoverRegions = vi.fn(async () => {});

  const commonProps = {
    open: overrides?.open ?? true,
    pageMode: overrides?.pageMode,
    config: overrides?.config === undefined ? baseConfig : overrides.config,
    health: overrides?.health ?? baseHealth,
    appSettings: overrides?.appSettings ?? baseSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    onAdvertise,
    meshDiscovery: overrides?.meshDiscovery ?? null,
    meshDiscoveryLoadingTarget: overrides?.meshDiscoveryLoadingTarget ?? null,
    onDiscoverMesh,
    regionDiscovery: overrides?.regionDiscovery ?? null,
    regionDiscoveryLoading: false,
    onDiscoverRegions,
    onHealthRefresh: vi.fn(async () => {}),
    onRefreshAppSettings,
    contacts: overrides?.contacts,
    trackedTelemetryRepeaters: overrides?.trackedTelemetryRepeaters,
  };

  const view = overrides?.externalSidebarNav
    ? render(
        <SettingsModal
          {...commonProps}
          externalSidebarNav
          desktopSection={overrides.desktopSection ?? 'radio'}
        />
      )
    : render(<SettingsModal {...commonProps} />);

  return {
    onSaveAppSettings,
    onRefreshAppSettings,
    onSave,
    onClose,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    onAdvertise,
    onDiscoverMesh,
    onDiscoverRegions,
    view,
  };
}

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: '(max-width: 767px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function openRadioSection() {
  const radioToggle = screen.getByRole('button', { name: i18n.t('settingsNav.radio') });
  fireEvent.click(radioToggle);
}

/**
 * The radio section is tabbed; controls live in exactly one of them.
 * Radix activates a tab on focus, which fireEvent.click does not produce in jsdom,
 * so drive mouseDown as well.
 */
function openRadioTab(key: 'tabConnection' | 'tabDevice' | 'tabMessaging' | 'tabAdvert') {
  const label = i18n.t(`settings.radio.${key}`);
  for (const tab of screen.getAllByRole('tab', { name: label })) {
    fireEvent.mouseDown(tab);
    fireEvent.click(tab);
  }
}

function openLocalSection() {
  const localToggle = screen.getByRole('button', { name: i18n.t('settingsNav.local') });
  fireEvent.click(localToggle);
}

function openDatabaseSection() {
  const databaseToggle = screen.getByRole('button', { name: i18n.t('settingsNav.database') });
  fireEvent.click(databaseToggle);
}

describe('SettingsModal', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getFanoutConfigs').mockResolvedValue([]);
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(baseTransport);
    vi.spyOn(api, 'updateRadioTransport').mockResolvedValue(baseTransport);
    vi.spyOn(api, 'getRadioProxy').mockResolvedValue({
      enabled: false,
      bind: '0.0.0.0',
      port: 5001,
      max_clients: 8,
      listening: false,
      client_count: 0,
      dropped_messages: 0,
      dropped_logs: 0,
      last_error: null,
      instance_id: 'abc123def456',
      model: 'MeshloomProxy/abc123def456',
    });
    vi.spyOn(api, 'updateRadioProxy').mockResolvedValue({
      enabled: true,
      bind: '0.0.0.0',
      port: 5001,
      max_clients: 8,
      listening: true,
      client_count: 0,
      dropped_messages: 0,
      dropped_logs: 0,
      last_error: null,
      instance_id: 'abc123def456',
      model: 'MeshloomProxy/abc123def456',
    });
    vi.spyOn(api, 'scanRadioBle').mockResolvedValue({ devices: [] });
    vi.spyOn(api, 'getCommunity').mockResolvedValue({
      enabled: false,
      locked: false,
      iata: '',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    window.location.hash = '';
    document.documentElement.style.fontSize = '';
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('refreshes app settings when opened', async () => {
    const { onRefreshAppSettings } = renderModal();

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('refreshes app settings in page mode even when open is false', async () => {
    const { onRefreshAppSettings } = renderModal({ open: false, pageMode: true });

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('does not render when closed outside page mode', () => {
    renderModal({ open: false });
    expect(screen.queryByLabelText(i18n.t('settings.radio.preset'))).not.toBeInTheDocument();
  });

  it('shows favorite-contact radio sync helper text in radio tab', async () => {
    renderModal();
    openRadioSection();
    openRadioTab('tabMessaging');

    expect(screen.getByText(i18n.t('settings.radio.maxContactsHelp'))).toBeInTheDocument();
  });

  it('renders flood and zero-hop advert buttons and passes the selected mode', async () => {
    const onAdvertise = vi.fn(async (_mode: RadioAdvertMode) => {});
    renderModal({ onAdvertise });
    openRadioSection();
    openRadioTab('tabAdvert');

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.sendFlood') }));
    await waitFor(() => {
      expect(onAdvertise).toHaveBeenCalledWith('flood');
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.sendZeroHop') }));
    await waitFor(() => {
      expect(onAdvertise).toHaveBeenCalledWith('zero_hop');
    });
  });

  it('shows the transport picker when config is null', async () => {
    renderModal({ config: null });

    const radioToggle = screen.getByRole('button', { name: i18n.t('settingsNav.radio') });
    expect(radioToggle).not.toBeDisabled();

    fireEvent.click(radioToggle);
    expect(
      await screen.findByRole('heading', {
        level: 4,
        name: i18n.t('settings.radio.transport'),
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.unavailableUntilConnected'))
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(i18n.t('settings.radio.preset'))).not.toBeInTheDocument();
  });

  it('shows the transport picker in sidebar-nav mode when config is null', async () => {
    renderModal({
      config: null,
      externalSidebarNav: true,
      desktopSection: 'radio',
    });

    expect(
      await screen.findByRole('heading', {
        level: 4,
        name: i18n.t('settings.radio.transport'),
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.unavailableUntilConnected'))
    ).toBeInTheDocument();
  });

  it('offers serial and bluetooth even when the host probe reports them unavailable', async () => {
    vi.mocked(api.getRadioTransport).mockResolvedValue({
      ...baseTransport,
      configured: false,
      transport: null,
      serial_port: '',
      serial_ports: [],
      capabilities: {
        tcp: true,
        serial: false,
        ble: false,
        serial_unavailable_reason: 'No serial ports are visible',
        ble_unavailable_reason: 'No Bluetooth adapter',
      },
    });
    renderModal({
      health: { ...baseHealth, transport_configured: false },
    });
    openRadioSection();
    openRadioTab('tabConnection');

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.radio.transportSerial') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.transportTcp') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.transportBle') })
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('settings.radio.serialUnavailable', {
          reason: 'No serial ports are visible',
        })
      )
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.transportBle') }));
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.bleScan') })
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.bleUnavailable', { reason: 'No Bluetooth adapter' }))
    ).toBeInTheDocument();
  });

  it('keeps a saved serial port visible when the host reports no ports', async () => {
    vi.mocked(api.getRadioTransport).mockResolvedValue({
      ...baseTransport,
      serial_ports: [],
      capabilities: {
        ...baseTransport.capabilities,
        serial: false,
        serial_unavailable_reason: 'pyserial missing',
      },
    });
    renderModal({
      health: { ...baseHealth, transport_configured: true },
    });
    openRadioSection();
    openRadioTab('tabConnection');

    const portSelect = await screen.findByLabelText(i18n.t('settings.radio.serialPort'));
    expect(portSelect).toHaveValue('/dev/ttyUSB0');
    expect(
      screen.getByText(new RegExp(i18n.t('settings.radio.serialPortUnavailable')))
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.serialUnavailable', { reason: 'pyserial missing' }))
    ).toBeInTheDocument();
  });

  it('does not reconnect from settings when transport is not configured', async () => {
    const { onReconnect } = renderModal({
      health: {
        ...baseHealth,
        radio_connected: false,
        radio_state: 'paused',
        transport_configured: false,
      },
    });
    openRadioSection();
    openRadioTab('tabConnection');

    expect(
      await screen.findByText(i18n.t('settings.radio.saveTransportFirst'))
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('settings.radio.reconnect') })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.reconnect') }));
    expect(onReconnect).not.toHaveBeenCalled();
  });

  it('saves the selected TCP transport from the radio connection section', async () => {
    renderModal();
    openRadioSection();
    openRadioTab('tabConnection');

    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.radio.transportTcp') })
    );
    fireEvent.change(screen.getByLabelText(i18n.t('settings.radio.tcpHost')), {
      target: { value: '10.0.0.8' },
    });
    fireEvent.change(screen.getByLabelText(i18n.t('settings.radio.tcpPort')), {
      target: { value: '5000' },
    });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.applyTransport') }));

    await waitFor(() => {
      expect(api.updateRadioTransport).toHaveBeenCalledWith({
        transport: 'tcp',
        tcp_host: '10.0.0.8',
        tcp_port: 5000,
      });
    });
  });

  it('shows cached radio firmware and capacity info under the connection status', () => {
    renderModal({
      health: {
        ...baseHealth,
        radio_device_info: {
          model: 'T-Echo',
          firmware_build: '2025-02-01',
          firmware_version: '1.2.3',
          max_contacts: 350,
          max_channels: 64,
        },
      },
    });
    openRadioSection();
    openRadioTab('tabConnection');

    expect(
      screen.getByText(
        i18n.t('settings.radio.deviceRunning', {
          model: 'T-Echo',
          firmware: '2025-02-01/1.2.3',
        }) +
          i18n.t('settings.radio.deviceMax', {
            capacity: `${i18n.t('settings.radio.contactsCount', { count: 350 })}, ${i18n.t('settings.radio.channelsCount', { count: 64 })}`,
          })
      )
    ).toBeInTheDocument();
  });

  it('shows reconnect action when radio connection is paused', () => {
    renderModal({
      health: { ...baseHealth, radio_state: 'paused' },
    });
    openRadioSection();
    openRadioTab('tabConnection');

    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.reconnect') })
    ).toBeInTheDocument();
  });

  it('runs repeater mesh discovery from the radio tab', async () => {
    const { onDiscoverMesh } = renderModal();
    openRadioSection();
    openRadioTab('tabAdvert');

    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.discoverRepeaters') })
    );

    await waitFor(() => {
      expect(onDiscoverMesh).toHaveBeenCalledWith('repeaters');
    });
  });

  it('renders mesh discovery results in the radio tab', () => {
    renderModal({
      meshDiscovery: {
        target: 'all',
        duration_seconds: 8,
        results: [
          {
            public_key: '11'.repeat(32),
            name: null,
            node_type: 'repeater',
            heard_count: 2,
            local_snr: 7.5,
            local_rssi: -101,
            remote_snr: 4,
          },
        ],
      },
    });
    openRadioSection();
    openRadioTab('tabAdvert');

    expect(screen.getByText(i18n.t('settings.radio.lastSweep', { count: 1 }))).toBeInTheDocument();
    expect(screen.getByText('repeater')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.radio.heardCount', { count: 2 }))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.listenWindow', { seconds: '8' }))
    ).toBeInTheDocument();
  });

  it('discovers regions using repeaters from the last mesh sweep', async () => {
    const { onDiscoverRegions } = renderModal({
      meshDiscovery: {
        target: 'all',
        duration_seconds: 8,
        results: [
          {
            public_key: '11'.repeat(32),
            name: 'RPT-A',
            node_type: 'repeater',
            heard_count: 1,
            local_snr: 5,
            local_rssi: -100,
            remote_snr: 3,
          },
          {
            public_key: '22'.repeat(32),
            name: 'Sensor',
            node_type: 'sensor',
            heard_count: 1,
            local_snr: 5,
            local_rssi: -100,
            remote_snr: 3,
          },
        ],
      },
    });
    openRadioSection();
    openRadioTab('tabMessaging');

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.discoverRegions') }));

    // Only the repeater's key is passed, not the sensor's.
    await waitFor(() => {
      expect(onDiscoverRegions).toHaveBeenCalledWith(['11'.repeat(32)]);
    });
  });

  it('adds discovered regions to the known-regions field', () => {
    renderModal({
      regionDiscovery: {
        repeaters_queried: 2,
        repeaters_answered: 2,
        regions: ['nl-gr', 'de-by'],
        results: [],
      },
    });
    openRadioSection();
    openRadioTab('tabMessaging');

    expect(
      screen.getByText(
        `${i18n.t('settings.radio.regionsAnswered', { answered: 2, queried: 2, count: 2 })}${i18n.t('settings.radio.regionsFound', { count: 2 })}`
      )
    ).toBeInTheDocument();

    const knownRegions = screen.getByLabelText(
      i18n.t('settings.radio.knownRegions')
    ) as HTMLTextAreaElement;
    fireEvent.change(knownRegions, { target: { value: 'nl-gr' } });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.addToKnown') }));

    // Existing 'nl-gr' preserved, only the new 'de-by' appended.
    expect(knownRegions.value).toBe('nl-gr\nde-by');
  });

  it('copies radio lat,lon from config via Share my location', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderModal();
    openRadioSection();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('share.shareLocation') }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('1.00000, 2.00000');
    });
  });

  it('disables Share my location when the radio has no coordinates', () => {
    renderModal({ config: { ...baseConfig, lat: 0, lon: 0 } });
    openRadioSection();
    expect(screen.getByRole('button', { name: i18n.t('share.shareLocation') })).toBeDisabled();
  });

  it('fills lat/lon from a map pick without saving radio config yet', async () => {
    const { onSave } = renderModal();
    openRadioSection();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.pickOnMap') }));
    fireEvent.click(await screen.findByRole('button', { name: 'apply-map-pick' }));

    expect(screen.getByLabelText(i18n.t('settings.radio.latitude'))).toHaveValue(45.764043);
    expect(screen.getByLabelText(i18n.t('settings.radio.longitude'))).toHaveValue(4.835659);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('saves advert location source through radio config save', async () => {
    const { onSave } = renderModal();
    openRadioSection();

    fireEvent.change(screen.getByLabelText(i18n.t('settings.radio.advertLocationSource')), {
      target: { value: 'off' },
    });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.saveConfig') }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ advert_location_source: 'off' })
      );
    });
  });

  it('saves multi-acks through radio config save', async () => {
    const { onSave } = renderModal();
    openRadioSection();

    fireEvent.click(screen.getByLabelText(i18n.t('settings.radio.multiAcks')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.saveConfig') }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ multi_acks_enabled: true }));
    });
  });

  it('saves changed max contacts value through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal();
    openRadioSection();
    openRadioTab('tabMessaging');

    const maxContactsInput = screen.getByLabelText(i18n.t('settings.radio.maxContacts'));
    fireEvent.change(maxContactsInput, { target: { value: '250' } });

    // Click the "Save Messaging Settings" button
    const saveButtons = screen.getAllByRole('button', {
      name: i18n.t('settings.radio.saveMessaging'),
    });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({ max_radio_contacts: 250 });
    });
  });

  it('does not save max contacts when unchanged', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, max_radio_contacts: 200 },
    });
    openRadioSection();
    openRadioTab('tabMessaging');

    // Click the "Save Messaging Settings" button
    const saveButtons = screen.getAllByRole('button', {
      name: i18n.t('settings.radio.saveMessaging'),
    });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).not.toHaveBeenCalled();
    });
  });

  it('renders Meshloom Community settings with a join CTA when community is off', async () => {
    vi.spyOn(api, 'getCommunity').mockResolvedValue({
      enabled: false,
      locked: false,
      iata: '',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'community',
    });

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyAccount'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('settings.community.enable'))).not.toBeDisabled();
    expect(screen.queryByText('Avancé')).not.toBeInTheDocument();
    expect(screen.queryByText('Advanced')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/broker/i)).not.toBeInTheDocument();
  });

  it('does not mention Meshloom Community MQTT inside Fanout', async () => {
    const { view } = renderModal({
      externalSidebarNav: true,
      desktopSection: 'fanout',
    });

    await waitFor(() => {
      expect(api.getFanoutConfigs).toHaveBeenCalled();
    });
    expect(view.container).not.toHaveTextContent(/Meshloom Community/);
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).not.toBeInTheDocument();
  });

  it('disables Meshloom Community enable when the operator locked it', async () => {
    vi.spyOn(api, 'getCommunity').mockResolvedValue({
      enabled: false,
      locked: true,
      iata: '',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'community',
    });

    expect(await screen.findByText(i18n.t('settings.community.locked'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('settings.community.enable'))).toBeDisabled();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeDisabled();
  });

  it('renders selected section from external sidebar nav on desktop mode', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'fanout',
    });

    await waitFor(() => {
      expect(api.getFanoutConfigs).toHaveBeenCalled();
    });
    expect(
      screen.getByRole('button', { name: i18n.t('settings.fanout.list.addIntegration') })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settingsNav.local') })
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(i18n.t('settings.radio.preset'))).not.toBeInTheDocument();
  });

  it('does not clip the fanout add-integration menu in external desktop mode', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'fanout',
    });

    const addIntegrationButton = await screen.findByRole('button', {
      name: i18n.t('settings.fanout.list.addIntegration'),
    });
    const wrapperSection = addIntegrationButton.closest('section');
    expect(wrapperSection).not.toHaveClass('overflow-hidden');
  });

  it('applies the centered 800px column layout to non-fanout settings content', () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'local',
    });

    const localSettingsText = screen.getByText(i18n.t('settings.local.deviceOnly'));
    expect(localSettingsText.closest('div')).toHaveClass('mx-auto', 'w-full', 'max-w-[800px]');
  });

  it('toggles sections in mobile accordion mode', () => {
    renderModal({ mobile: true });
    const localToggle = screen.getAllByRole('button', { name: i18n.t('settingsNav.local') })[0];

    expect(screen.queryByLabelText(i18n.t('settings.radio.preset'))).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(i18n.t('settings.local.localLabelText'))
    ).not.toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(screen.getByLabelText(i18n.t('settings.local.localLabelText'))).toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(
      screen.queryByLabelText(i18n.t('settings.local.localLabelText'))
    ).not.toBeInTheDocument();
  });

  it('lists proxy after radio in the accordion nav', async () => {
    renderModal({ mobile: true });
    const sectionToggles = screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('aria-expanded') !== null);
    const labels = sectionToggles.map((button) => button.textContent ?? '');
    const radioIdx = labels.findIndex((label) => label.includes(i18n.t('settingsNav.radio')));
    const proxyIdx = labels.findIndex((label) => label.includes(i18n.t('settingsNav.proxy')));

    expect(radioIdx).toBeGreaterThanOrEqual(0);
    expect(proxyIdx).toBe(radioIdx + 1);

    fireEvent.click(sectionToggles[proxyIdx]);
    expect(await screen.findByText(i18n.t('settings.proxy.title'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('settings.proxy.enabled'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.proxy.warning'))).toBeInTheDocument();
  });

  it('lists notifications after local in the accordion nav', () => {
    renderModal({ mobile: true });
    const sectionToggles = screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('aria-expanded') !== null);
    const labels = sectionToggles.map((button) => button.textContent ?? '');
    const localIdx = labels.findIndex((label) => label.includes(i18n.t('settingsNav.local')));
    const notificationsIdx = labels.findIndex((label) =>
      label.includes(i18n.t('settingsNav.notifications'))
    );

    expect(localIdx).toBeGreaterThanOrEqual(0);
    expect(notificationsIdx).toBe(localIdx + 1);

    fireEvent.click(sectionToggles[notificationsIdx]);
    expect(screen.getByText(i18n.t('settings.notifications.thisDevice'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.notifications.defaults'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.notifications.exceptions'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.notifications.vapidSubject'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.notifications.ossUpdate'))).toBeInTheDocument();
  });

  it('lists the new Windows 95 and iPhone themes', () => {
    renderModal();
    openLocalSection();

    expect(screen.getByText(i18n.t('settings.local.themes.windows-95'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.local.themes.ios'))).toBeInTheDocument();
  });

  it('reverts checkbox state when auto-persist fails on the database section', async () => {
    // Auto-persist replaced the old "Save Settings" button on this section.
    // The risk is now: a toggle gets applied optimistically, the PATCH fails,
    // and we're left with the UI out of sync with saved state. Verify the
    // revert-on-error path keeps the checkbox consistent with the server.
    const onSaveAppSettings = vi.fn(async () => {
      throw new Error('Save failed');
    });

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'database',
      onSaveAppSettings,
    });

    const checkbox = screen.getByRole('checkbox', {
      name: i18n.t('settings.database.autoDecrypt'),
    }) as HTMLInputElement;
    const initialChecked = checkbox.checked;

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(checkbox.checked).toBe(initialChecked);
    });
  });

  it('serializes rapid auto-persist clicks so stale writes cannot win', async () => {
    // Regression test for a race where rapid consecutive checkbox toggles
    // fire overlapping PATCHes that can land out of order. The page now
    // chains saves through a single promise, so the server sees them in
    // the order the user clicked. This test hand-controls resolution
    // order to force the "stale write" scenario if serialization were off.

    const deferred: { resolve: () => void }[] = [];
    const callOrder: number[] = [];

    const onSaveAppSettings = vi.fn(async (_update: unknown) => {
      const index = deferred.length;
      callOrder.push(index);
      await new Promise<void>((res) => {
        deferred.push({ resolve: res });
      });
    });

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'radio-app',
      onSaveAppSettings,
    });

    // Two distinct checkboxes in quick succession.
    const blockClients = screen.getByRole('checkbox', {
      name: i18n.t('settings.radioApp.blockClients'),
    });
    const blockRepeaters = screen.getByRole('checkbox', {
      name: i18n.t('settings.radioApp.blockRepeaters'),
    });

    fireEvent.click(blockClients);
    fireEvent.click(blockRepeaters);

    // Wait for the first PATCH to be registered. Only the first should be
    // in-flight — the second must be queued behind it.
    await waitFor(() => {
      expect(deferred.length).toBe(1);
    });
    expect(callOrder).toEqual([0]);

    // Resolve the first PATCH. The chain should now dispatch the second.
    deferred[0].resolve();
    await waitFor(() => {
      expect(deferred.length).toBe(2);
    });
    expect(callOrder).toEqual([0, 1]);

    // Resolve the second so the test tears down cleanly.
    deferred[1].resolve();
    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledTimes(2);
    });
  });

  it('does not call onClose after save/reboot flows in page mode', async () => {
    const onClose = vi.fn();
    const onSave = vi.fn(async () => {});
    const onSetPrivateKey = vi.fn(async () => {});
    const onReboot = vi.fn(async () => {});

    renderModal({
      pageMode: true,
      onClose,
      onSave,
      onSetPrivateKey,
      onReboot,
      onDisconnect: vi.fn(async () => {}),
      onReconnect: vi.fn(async () => {}),
    });
    openRadioSection();

    // Saving is offered only once something has actually changed.
    fireEvent.change(screen.getByLabelText(i18n.t('settings.radio.radioName')), {
      target: { value: 'Renamed node' },
    });

    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.saveConfigReboot') })
    );
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onReboot).toHaveBeenCalledTimes(1);
    });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(i18n.t('settings.radio.setPrivateKey')), {
      target: { value: 'a'.repeat(64) },
    });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.setKeyReboot') }));

    await waitFor(() => {
      expect(onSetPrivateKey).toHaveBeenCalledWith('a'.repeat(64));
      expect(onReboot).toHaveBeenCalledTimes(2);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('defaults the language control to French and persists the pref', async () => {
    renderModal();
    openLocalSection();

    const select = screen.getByLabelText(i18n.t('language.label')) as HTMLSelectElement;
    expect(select.value).toBe('fr');

    fireEvent.change(select, { target: { value: 'en' } });

    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
    expect(i18n.language).toBe('en');
    expect(screen.getByLabelText(i18n.t('language.label'))).toHaveValue('en');

    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('stores and clears reopen-last-conversation preference locally', () => {
    window.location.hash = '#raw';
    renderModal();
    openLocalSection();

    const checkbox = screen.getByLabelText(i18n.t('settings.local.reopenLast'));
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBe('1');
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toContain('"type":"raw"');

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBeNull();
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toBeNull();
  });

  it('defaults the path-hop-width toggle to on and persists disabling it', () => {
    renderModal();
    openLocalSection();

    const checkbox = screen.getByLabelText(i18n.t('settings.local.pathHopWidth'));
    expect(checkbox).toBeChecked();
    expect(localStorage.getItem(SHOW_PATH_HOP_WIDTH_KEY)).toBeNull();

    fireEvent.click(checkbox);

    expect(localStorage.getItem(SHOW_PATH_HOP_WIDTH_KEY)).toBe('false');
  });

  it('defaults distance units to metric and stores local changes', () => {
    renderModal();
    openLocalSection();

    const select = screen.getByLabelText(i18n.t('settings.local.distanceUnits'));
    expect(select).toHaveValue('metric');

    fireEvent.change(select, { target: { value: 'smoots' } });

    expect(localStorage.getItem(DISTANCE_UNIT_KEY)).toBe('smoots');
  });

  it('defaults relative font size to 100% and exposes the expected input bounds', () => {
    renderModal();
    openLocalSection();

    const slider = screen.getByLabelText(i18n.t('settings.local.fontSizeSlider'));
    const input = screen.getByLabelText(i18n.t('settings.local.fontSizePercent'));

    expect(slider).toHaveValue(String(DEFAULT_FONT_SCALE));
    expect(slider).toHaveAttribute('step', '5');
    expect(input).toHaveValue(DEFAULT_FONT_SCALE);
    expect(input).toHaveAttribute('min', String(MIN_FONT_SCALE));
    expect(input).toHaveAttribute('max', String(MAX_FONT_SCALE));
  });

  it('stores and applies relative font size changes locally', async () => {
    renderModal();
    openLocalSection();

    const slider = screen.getByLabelText(i18n.t('settings.local.fontSizeSlider'));

    fireEvent.change(slider, { target: { value: '135' } });

    expect(localStorage.getItem(FONT_SCALE_KEY)).toBeNull();
    expect(document.documentElement.style.fontSize).toBe('');

    fireEvent.mouseUp(slider);

    await waitFor(() => {
      expect(localStorage.getItem(FONT_SCALE_KEY)).toBe('135');
      expect(document.documentElement.style.fontSize).toBe('135%');
    });

    fireEvent.change(screen.getByLabelText(i18n.t('settings.local.fontSizePercent')), {
      target: { value: '137.5' },
    });

    await waitFor(() => {
      expect(localStorage.getItem(FONT_SCALE_KEY)).toBe('137.5');
      expect(document.documentElement.style.fontSize).toBe('137.5%');
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.local.reset') }));

    await waitFor(() => {
      expect(localStorage.getItem(FONT_SCALE_KEY)).toBeNull();
      expect(document.documentElement.style.fontSize).toBe('100%');
    });
  });

  it('purges decrypted raw packets via maintenance endpoint action', async () => {
    const runMaintenanceSpy = vi.spyOn(api, 'runMaintenance').mockResolvedValue({
      packets_deleted: 12,
      vacuumed: true,
    });

    renderModal();
    openDatabaseSection();

    expect(screen.getByText(i18n.t('settings.backupDbHelp'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.database.purgeArchivalHelp'))).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.database.purgeArchivalButton') })
    );

    await waitFor(() => {
      expect(runMaintenanceSpy).toHaveBeenCalledWith({ purgeLinkedRawPackets: true });
    });
  });

  it('renders statistics section with fetched data', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [
        { channel_key: 'AA'.repeat(16), channel_name: 'general', message_count: 42 },
      ],
      contact_count: 10,
      repeater_count: 3,
      channel_count: 5,
      total_packets: 200,
      decrypted_packets: 150,
      undecrypted_packets: 50,
      total_dms: 25,
      total_channel_messages: 80,
      total_outgoing: 30,
      contacts_heard: { last_hour: 2, last_24_hours: 7, last_week: 10 },
      repeaters_heard: { last_hour: 1, last_24_hours: 3, last_week: 3 },
      known_channels_active: { last_hour: 1, last_24_hours: 4, last_week: 6 },
      path_hash_width_24h: {
        total_packets: 120,
        single_byte: 60,
        double_byte: 36,
        triple_byte: 24,
        single_byte_pct: 50,
        double_byte_pct: 30,
        triple_byte_pct: 20,
      },
      region_scope_24h: {
        total_messages: 120,
        scoped_messages: 40,
        scoped_pct: 33.3,
        false_positive_floor: 2,
        total_senders: 12,
        scoped_senders: 3,
        scoped_senders_pct: 25,
      },
      packets_per_hour_72h: [
        { timestamp: 1711792800, count: 12 },
        { timestamp: 1711796400, count: 8 },
      ],
      noise_floor_24h: {
        sample_interval_seconds: 60,
        coverage_seconds: 3600,
        latest_noise_floor_dbm: -105,
        latest_timestamp: 1711800000,
        samples: [],
      },
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'statistics',
    });

    await waitFor(() => {
      expect(screen.getByText(i18n.t('settings.stats.network'))).toBeInTheDocument();
    });

    // Verify key labels are present
    expect(screen.getByText(i18n.t('settings.stats.contacts'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.repeaters'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.dms'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.channelMessages'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.outgoing'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.totalStored'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.decrypted'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.undecrypted'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.pathHashWidth'))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.stats.pathHashHelp', { count: 120 }))
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.contactsHeard'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.repeatersHeard'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.channelsActive'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.busiest'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.noiseFloor'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.stats.regionScope'))).toBeInTheDocument();
    // Fractions, not bare percentages — the sample size matters at this sparsity
    expect(
      screen.getByText(
        i18n.t('settings.stats.ofTotal', {
          scoped: (40).toLocaleString(),
          total: (120).toLocaleString(),
        }),
        { exact: false }
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('settings.stats.ofTotal', {
          scoped: (3).toLocaleString(),
          total: (12).toLocaleString(),
        }),
        { exact: false }
      )
    ).toBeInTheDocument();
    // 40 scoped is well above the floor of 2, so the percentage is shown
    expect(screen.getByText(/33\.3%/)).toBeInTheDocument();
    expect(
      screen.queryByText(i18n.t('settings.stats.floorNoise', { floor: '2' }))
    ).not.toBeInTheDocument();
  });

  it('discloses the false-positive floor and withholds a sub-0.1% scoped share', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [],
      contact_count: 0,
      repeater_count: 0,
      channel_count: 0,
      total_packets: 0,
      decrypted_packets: 0,
      undecrypted_packets: 0,
      total_dms: 0,
      total_channel_messages: 0,
      total_outgoing: 0,
      contacts_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      repeaters_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      known_channels_active: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      path_hash_width_24h: {
        total_packets: 0,
        single_byte: 0,
        double_byte: 0,
        triple_byte: 0,
        single_byte_pct: 0,
        double_byte_pct: 0,
        triple_byte_pct: 0,
      },
      // Mirrors real-world data: 70 "scoped" packets against a measured floor of
      // 60 is corrupt-capture noise, not adoption.
      region_scope_24h: {
        total_messages: 391757,
        scoped_messages: 70,
        scoped_pct: 0.0179,
        false_positive_floor: 60.3,
        total_senders: 117,
        scoped_senders: 3,
        scoped_senders_pct: 2.56,
      },
      packets_per_hour_72h: [],
      noise_floor_24h: {
        sample_interval_seconds: 60,
        coverage_seconds: 0,
        latest_noise_floor_dbm: null,
        latest_timestamp: null,
        samples: [],
      },
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({ externalSidebarNav: true, desktopSection: 'statistics' });

    await waitFor(() => {
      expect(screen.getByText(i18n.t('settings.stats.regionScope'))).toBeInTheDocument();
    });

    // 70 scoped sits just above the 60.3 floor, so most of it is corrupt captures
    expect(
      screen.getByText(i18n.t('settings.stats.floorIncludes', { floor: '60' }), { exact: false })
    ).toBeInTheDocument();
    // 0.0179% would render as a meaningless "0.0%", so the share is withheld
    expect(screen.queryByText(/\b0\.0%\b/)).not.toBeInTheDocument();
    expect(
      screen.getByText((text) => /70/.test(text) && /391/.test(text) && /757/.test(text))
    ).toBeInTheDocument();
    // ...but the decryption-backed sender figure still stands
    expect(
      screen.getByText(
        i18n.t('settings.stats.ofTotal', {
          scoped: (3).toLocaleString(),
          total: (117).toLocaleString(),
        }),
        { exact: false }
      )
    ).toBeInTheDocument();
    expect(screen.getByText(/2\.6%/)).toBeInTheDocument();
  });

  it('reports scoped traffic as noise when it is at or below the floor', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [],
      contact_count: 0,
      repeater_count: 0,
      channel_count: 0,
      total_packets: 0,
      decrypted_packets: 0,
      undecrypted_packets: 0,
      total_dms: 0,
      total_channel_messages: 0,
      total_outgoing: 0,
      contacts_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      repeaters_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      known_channels_active: { last_hour: 0, last_24_hours: 0, last_week: 0 },
      path_hash_width_24h: {
        total_packets: 0,
        single_byte: 0,
        double_byte: 0,
        triple_byte: 0,
        single_byte_pct: 0,
        double_byte_pct: 0,
        triple_byte_pct: 0,
      },
      region_scope_24h: {
        total_messages: 5000,
        scoped_messages: 12,
        scoped_pct: 0.24,
        false_positive_floor: 20,
        total_senders: 40,
        scoped_senders: 0,
        scoped_senders_pct: 0,
      },
      packets_per_hour_72h: [],
      noise_floor_24h: {
        sample_interval_seconds: 60,
        coverage_seconds: 0,
        latest_noise_floor_dbm: null,
        latest_timestamp: null,
        samples: [],
      },
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({ externalSidebarNav: true, desktopSection: 'statistics' });

    await waitFor(() => {
      expect(screen.getByText(i18n.t('settings.stats.regionScope'))).toBeInTheDocument();
    });

    expect(
      screen.getByText(i18n.t('settings.stats.floorNoise', { floor: '20' }), { exact: false })
    ).toBeInTheDocument();
    // Percentage withheld even though 0.24% would round visibly — it is noise
    expect(screen.queryByText(/0\.2%/)).not.toBeInTheDocument();
  });

  it('fetches statistics when expanded in mobile external-nav mode', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [],
      contact_count: 10,
      repeater_count: 3,
      channel_count: 5,
      total_packets: 200,
      decrypted_packets: 150,
      undecrypted_packets: 50,
      total_dms: 25,
      total_channel_messages: 80,
      total_outgoing: 30,
      contacts_heard: { last_hour: 2, last_24_hours: 7, last_week: 10 },
      repeaters_heard: { last_hour: 1, last_24_hours: 3, last_week: 3 },
      known_channels_active: { last_hour: 1, last_24_hours: 4, last_week: 6 },
      path_hash_width_24h: {
        total_packets: 120,
        single_byte: 60,
        double_byte: 36,
        triple_byte: 24,
        single_byte_pct: 50,
        double_byte_pct: 30,
        triple_byte_pct: 20,
      },
      region_scope_24h: {
        total_messages: 0,
        scoped_messages: 0,
        scoped_pct: 0,
        false_positive_floor: 0,
        total_senders: 0,
        scoped_senders: 0,
        scoped_senders_pct: 0,
      },
      packets_per_hour_72h: [],
      noise_floor_24h: {
        sample_interval_seconds: 60,
        coverage_seconds: 0,
        latest_noise_floor_dbm: null,
        latest_timestamp: null,
        samples: [],
      },
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    // Sections are navigated one at a time, so showing another one is what must not
    // fetch; the fetch belongs to statistics being on screen.
    const { view } = renderModal({
      mobile: true,
      externalSidebarNav: true,
      desktopSection: 'radio',
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    view.unmount();
    renderModal({
      mobile: true,
      externalSidebarNav: true,
      desktopSection: 'statistics',
    });

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith('./api/statistics', expect.any(Object));
    });

    await waitFor(() => {
      expect(screen.getByText(i18n.t('settings.stats.network'))).toBeInTheDocument();
    });
  });

  it('renders routed hourly checkbox and calls save on toggle', async () => {
    const onSaveAppSettings = vi.fn(async () => {});

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'radio-app',
      onSaveAppSettings,
    });

    const checkbox = screen.getByRole('checkbox', {
      name: (accessibleName) =>
        accessibleName.includes(i18n.t('settings.radioApp.pollRoutedHourly')) &&
        accessibleName.includes(i18n.t('settings.radioApp.pollRoutedHourlyHelp')),
    }) as HTMLInputElement;

    expect(checkbox).toBeInTheDocument();
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith(
        expect.objectContaining({ telemetry_routed_hourly: true })
      );
    });
  });

  it('says the directory is off when Meshloom Community is off', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'radio-app',
    });

    const callout = await screen.findByTestId('directory-off');
    expect(callout).toHaveTextContent(i18n.t('settings.directoryOff'));
    expect(screen.queryByTestId('directory-via-community')).not.toBeInTheDocument();
  });

  it('routes the directory through Community and offers no origin to point at', async () => {
    vi.spyOn(api, 'getCommunity').mockResolvedValue({
      enabled: true,
      locked: false,
      iata: 'LYS',
      broker_host: 'mqtt.meshloom.app',
      api_base: 'https://api.meshloom.app',
      publisher_configured: true,
      publisher_connected: true,
      env_seeded: false,
    });

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'radio-app',
      appSettings: {
        ...baseSettings,
        directory_available: true,
      },
    });

    const callout = await screen.findByTestId('directory-via-community');
    expect(callout).toHaveTextContent(i18n.t('settings.directoryViaStats'));
    expect(screen.queryByLabelText(/corescope/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/corescope/i)).not.toBeInTheDocument();
  });

  it('shows route badge per tracked repeater', async () => {
    const directKey = 'bb'.repeat(32);

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'radio-app',
      appSettings: {
        ...baseSettings,
        tracked_telemetry_repeaters: [directKey],
      },
      trackedTelemetryRepeaters: [directKey],
      contacts: [
        {
          public_key: directKey,
          name: 'DirectRepeater',
          type: 2,
          flags: 0,
          direct_path: 'aabb',
          direct_path_len: 1,
          direct_path_hash_mode: 1,
          last_advert: null,
          lat: null,
          lon: null,
          last_seen: null,
          on_radio: false,
          favorite: false,
          last_contacted: null,
          last_read_at: null,
          first_seen: null,
          effective_route: { path: 'aabb', path_len: 1, path_hash_mode: 1 },
          effective_route_source: 'direct',
        },
      ],
    });

    expect(screen.getByText('DirectRepeater')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.radioApp.routeDirect'))).toBeInTheDocument();
  });
});
