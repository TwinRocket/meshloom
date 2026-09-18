import { lazy, Suspense, useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { ChevronDown, Download, Map, MapPinned, Share2, Upload } from 'lucide-react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import {
  AdvancedBlock,
  DirtyDot,
  SettingsField,
  SettingsGroup,
  SettingsGroupHeader,
} from './settingsPrimitives';
import { toast } from '../ui/sonner';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { api, formatApiError } from '../../api';
import { RADIO_PRESETS } from '../../utils/radioPresets';
import { formatPickedCoordinate } from '../../utils/locationPicker';
import { stripRegionScopePrefix } from '../../utils/regionScope';
import {
  isRadioIdentityGate,
  type AppSettings,
  type AppSettingsUpdate,
  type HealthStatus,
  type RadioAdvertMode,
  type RadioBleDeviceInfo,
  type RadioConfig,
  type RadioConfigUpdate,
  type RadioDiscoveryResponse,
  type RadioDiscoveryTarget,
  type RadioRegionDiscoveryResponse,
  type RadioStatsSnapshot,
  type RadioTransportConfig,
  type RadioTransportKind,
  type RadioTransportUpdate,
} from '../../types';

const LocationPickerModal = lazy(() =>
  import('../LocationPickerModal').then((m) => ({ default: m.LocationPickerModal }))
);

const EMPTY_TRANSPORT: RadioTransportConfig = {
  configured: false,
  transport: null,
  serial_port: '',
  serial_baudrate: 115200,
  tcp_host: '',
  tcp_port: 5000,
  ble_address: '',
  ble_pin_configured: false,
  bound_public_key: null,
  capabilities: {
    tcp: true,
    serial: true,
    ble: true,
    serial_unavailable_reason: null,
    ble_unavailable_reason: null,
  },
  serial_ports: [],
};

function defaultKind(transport: RadioTransportConfig): RadioTransportKind {
  return transport.transport ?? 'serial';
}

function canShareRadioLocation(lat: number, lon: number): boolean {
  if (lat === 0 && lon === 0) return false;
  return lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}

function formatUptime(secs: number): string {
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatAirtime(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const hours = Math.floor(secs / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function StatRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-xs font-mono tabular-nums ${warn ? 'text-warning font-semibold' : ''}`}
      >
        {value}
      </span>
    </div>
  );
}

function RadioDetailsCollapsible({ stats }: { stats: RadioStatsSnapshot }) {
  const { t } = useTranslation();
  const age = stats.timestamp ? Math.max(0, Math.floor(Date.now() / 1000) - stats.timestamp) : null;
  const packets = {
    recv: stats.packets_recv,
    sent: stats.packets_sent,
    flood_tx: stats.flood_tx,
    direct_tx: stats.direct_tx,
    flood_rx: stats.flood_rx,
    direct_rx: stats.direct_rx,
  };

  return (
    <details className="group">
      <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
        <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
        {t('settings.radio.details')}
      </summary>
      <div className="mt-2 space-y-2 rounded-md border border-input bg-muted/20 p-3">
        {age !== null && (
          <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            {age < 5 ? t('settings.radio.updatedJustNow') : t('settings.radio.updatedAgo', { age })}
          </p>
        )}

        {/* Core */}
        {stats.uptime_secs != null && (
          <StatRow label={t('settings.radio.uptime')} value={formatUptime(stats.uptime_secs)} />
        )}
        {stats.battery_mv != null && stats.battery_mv > 0 && (
          <StatRow
            label={t('settings.radio.battery')}
            value={`${(stats.battery_mv / 1000).toFixed(2)}V`}
          />
        )}
        {stats.queue_len != null && (
          <StatRow
            label={t('settings.radio.txQueue')}
            value={`${stats.queue_len} / 16`}
            warn={stats.queue_len >= 14}
          />
        )}
        {stats.errors != null && (
          <StatRow
            label={t('settings.radio.errors')}
            value={String(stats.errors)}
            warn={stats.errors > 0}
          />
        )}

        {/* RF */}
        {stats.noise_floor != null && (
          <StatRow label={t('settings.radio.noiseFloor')} value={`${stats.noise_floor} dBm`} />
        )}
        {stats.last_rssi != null && (
          <StatRow label={t('settings.radio.lastRssi')} value={`${stats.last_rssi} dBm`} />
        )}
        {stats.last_snr != null && (
          <StatRow label={t('settings.radio.lastSnr')} value={`${stats.last_snr} dB`} />
        )}

        {/* Airtime */}
        {(stats.tx_air_secs != null || stats.rx_air_secs != null) && (
          <>
            {stats.tx_air_secs != null && (
              <StatRow
                label={t('settings.radio.txAirtime')}
                value={formatAirtime(stats.tx_air_secs)}
              />
            )}
            {stats.rx_air_secs != null && (
              <StatRow
                label={t('settings.radio.rxAirtime')}
                value={formatAirtime(stats.rx_air_secs)}
              />
            )}
          </>
        )}

        {/* Packets */}
        {packets.recv != null && (
          <StatRow label={t('settings.radio.packetsReceived')} value={String(packets.recv)} />
        )}
        {packets.sent != null && (
          <StatRow label={t('settings.radio.packetsSent')} value={String(packets.sent)} />
        )}
        {packets.flood_tx != null && (
          <StatRow label={t('settings.radio.floodTx')} value={String(packets.flood_tx)} />
        )}
        {packets.flood_rx != null && (
          <StatRow label={t('settings.radio.floodRx')} value={String(packets.flood_rx)} />
        )}
        {packets.direct_tx != null && (
          <StatRow label={t('settings.radio.directTx')} value={String(packets.direct_tx)} />
        )}
        {packets.direct_rx != null && (
          <StatRow label={t('settings.radio.directRx')} value={String(packets.direct_rx)} />
        )}
      </div>
    </details>
  );
}

function RadioTransportPanel({
  health,
  onHealthRefresh,
}: {
  health: HealthStatus | null;
  onHealthRefresh?: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<RadioTransportConfig>(EMPTY_TRANSPORT);
  const [kind, setKind] = useState<RadioTransportKind>('serial');
  const [serialPort, setSerialPort] = useState('');
  const [serialBaudrate, setSerialBaudrate] = useState('115200');
  const [tcpHost, setTcpHost] = useState('');
  const [tcpPort, setTcpPort] = useState('5000');
  const [bleAddress, setBleAddress] = useState('');
  const [blePin, setBlePin] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [bleDevices, setBleDevices] = useState<RadioBleDeviceInfo[]>([]);

  const applySnapshot = (next: RadioTransportConfig) => {
    setSnapshot(next);
    setKind(defaultKind(next));
    setSerialPort(next.serial_port);
    setSerialBaudrate(String(next.serial_baudrate || 115200));
    setTcpHost(next.tcp_host);
    setTcpPort(String(next.tcp_port || 5000));
    setBleAddress(next.ble_address);
    setBlePin('');
    setBleDevices([]);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const next = await api.getRadioTransport();
        if (!cancelled) {
          applySnapshot(next);
          setLoadError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(formatApiError(err, t) || t('settings.radio.loadTransportFailed'));
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const capabilities = snapshot.capabilities;
  const savedPortMissing =
    Boolean(serialPort) && !snapshot.serial_ports.some((port) => port.path === serialPort);

  const handleRefreshHost = async () => {
    setRefreshing(true);
    setSaveError(null);
    try {
      const next = await api.getRadioTransport();
      setSnapshot(next);
    } catch (err) {
      setSaveError(formatApiError(err, t) || t('settings.radio.loadTransportFailed'));
    } finally {
      setRefreshing(false);
    }
  };

  const handleScanBle = async () => {
    setScanning(true);
    setSaveError(null);
    try {
      const result = await api.scanRadioBle();
      setBleDevices(result.devices);
      if (result.devices.length === 0) {
        toast.info(t('settings.radio.bleNoDevices'));
      }
    } catch (err) {
      setSaveError(formatApiError(err, t) || t('settings.radio.transportSaveFailed'));
    } finally {
      setScanning(false);
    }
  };

  const handleApply = async () => {
    setSaveError(null);
    setBusy(true);
    try {
      const body: RadioTransportUpdate = { transport: kind };
      if (kind === 'serial') {
        body.serial_port = serialPort;
        const baud = parseInt(serialBaudrate, 10);
        if (!Number.isNaN(baud)) body.serial_baudrate = baud;
      } else if (kind === 'tcp') {
        body.tcp_host = tcpHost;
        const port = parseInt(tcpPort, 10);
        if (!Number.isNaN(port)) body.tcp_port = port;
      } else {
        body.ble_address = bleAddress;
        if (blePin.trim()) body.ble_pin = blePin.trim();
      }
      const next = await api.updateRadioTransport(body);
      applySnapshot(next);
      toast.success(t('settings.radio.transportSaved'));
      await onHealthRefresh?.();
    } catch (err) {
      setSaveError(formatApiError(err, t) || t('settings.radio.transportSaveFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">{t('settings.radio.transport')}</h4>
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.transportHelp')}
      </p>
      {loadError && (
        <p className="text-sm text-destructive" role="alert">
          {loadError}
        </p>
      )}
      <fieldset className="space-y-2">
        <legend className="sr-only">{t('settings.radio.transport')}</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <Button
            type="button"
            variant="outline"
            aria-pressed={kind === 'serial'}
            className={kind === 'serial' ? 'border-primary/50 bg-primary/10' : ''}
            onClick={() => setKind('serial')}
          >
            {t('settings.radio.transportSerial')}
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-pressed={kind === 'tcp'}
            className={kind === 'tcp' ? 'border-primary/50 bg-primary/10' : ''}
            onClick={() => setKind('tcp')}
          >
            {t('settings.radio.transportTcp')}
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-pressed={kind === 'ble'}
            className={kind === 'ble' ? 'border-primary/50 bg-primary/10' : ''}
            onClick={() => setKind('ble')}
          >
            {t('settings.radio.transportBle')}
          </Button>
        </div>
      </fieldset>

      {kind === 'serial' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="radio-serial-port">{t('settings.radio.serialPort')}</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleRefreshHost()}
              disabled={refreshing}
            >
              {refreshing
                ? t('settings.radio.serialRefreshing')
                : t('settings.radio.serialRefresh')}
            </Button>
          </div>
          <select
            id="radio-serial-port"
            value={serialPort}
            onChange={(e) => setSerialPort(e.target.value)}
            className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="">{t('settings.radio.serialAutoDetect')}</option>
            {snapshot.serial_ports.map((port) => (
              <option key={port.path} value={port.path}>
                {port.description ? `${port.path} — ${port.description}` : port.path}
              </option>
            ))}
            {savedPortMissing && (
              <option value={serialPort}>
                {serialPort} ({t('settings.radio.serialPortUnavailable')})
              </option>
            )}
          </select>
          {capabilities.serial_unavailable_reason && (
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.serialUnavailable', {
                reason: capabilities.serial_unavailable_reason,
              })}
            </p>
          )}
          <Label htmlFor="radio-serial-baud">{t('settings.radio.serialBaudrate')}</Label>
          <Input
            id="radio-serial-baud"
            type="number"
            value={serialBaudrate}
            onChange={(e) => setSerialBaudrate(e.target.value)}
          />
        </div>
      )}

      {kind === 'tcp' && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="radio-tcp-host">{t('settings.radio.tcpHost')}</Label>
            <Input
              id="radio-tcp-host"
              value={tcpHost}
              onChange={(e) => setTcpHost(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="radio-tcp-port">{t('settings.radio.tcpPort')}</Label>
            <Input
              id="radio-tcp-port"
              type="number"
              value={tcpPort}
              onChange={(e) => setTcpPort(e.target.value)}
            />
          </div>
        </div>
      )}

      {kind === 'ble' && (
        <div className="space-y-2">
          <Label htmlFor="radio-ble-address">{t('settings.radio.bleAddress')}</Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="radio-ble-address"
              value={bleAddress}
              onChange={(e) => setBleAddress(e.target.value)}
              className="font-mono"
            />
            <Button type="button" variant="outline" onClick={handleScanBle} disabled={scanning}>
              {scanning ? t('settings.radio.bleScanning') : t('settings.radio.bleScan')}
            </Button>
          </div>
          {capabilities.ble_unavailable_reason && (
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.bleUnavailable', {
                reason: capabilities.ble_unavailable_reason,
              })}
            </p>
          )}
          {bleDevices.length > 0 && (
            <div className="space-y-1">
              {bleDevices.map((device) => (
                <Button
                  key={device.address}
                  type="button"
                  variant="outline"
                  className="w-full justify-start font-mono text-xs"
                  onClick={() => setBleAddress(device.address)}
                >
                  {device.name ? `${device.name} — ${device.address}` : device.address}
                </Button>
              ))}
            </div>
          )}
          <Label htmlFor="radio-ble-pin">{t('settings.radio.blePin')}</Label>
          <Input
            id="radio-ble-pin"
            type="password"
            autoComplete="off"
            value={blePin}
            onChange={(e) => setBlePin(e.target.value)}
            placeholder={t('settings.radio.blePinPlaceholder')}
          />
          {snapshot.ble_pin_configured && (
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.blePinConfigured')}
            </p>
          )}
        </div>
      )}

      {saveError && (
        <p className="text-sm text-destructive" role="alert">
          {saveError}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        onClick={handleApply}
        disabled={busy}
        className="w-full"
      >
        {busy ? t('settings.radio.applyingTransport') : t('settings.radio.applyTransport')}
      </Button>
      {health?.transport_configured === false && (
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.saveTransportFirst')}
        </p>
      )}
    </div>
  );
}

export function SettingsRadioSection({
  config,
  health,
  appSettings,
  pageMode,
  onSave,
  onSaveAppSettings,
  onSetPrivateKey,
  onReboot,
  onDisconnect,
  onReconnect,
  onAdvertise,
  meshDiscovery,
  meshDiscoveryLoadingTarget,
  onDiscoverMesh,
  regionDiscovery,
  regionDiscoveryLoading,
  onDiscoverRegions,
  onHealthRefresh,
  onClose,
  className,
}: {
  config: RadioConfig | null;
  health: HealthStatus | null;
  appSettings: AppSettings | null;
  pageMode: boolean;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  onReconnect: () => Promise<void>;
  onAdvertise: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget: RadioDiscoveryTarget | null;
  onDiscoverMesh: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery: RadioRegionDiscoveryResponse | null;
  regionDiscoveryLoading: boolean;
  onDiscoverRegions: (publicKeys?: string[]) => Promise<void>;
  onHealthRefresh?: () => Promise<void>;
  onClose: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  // Radio config state
  const [name, setName] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [txPower, setTxPower] = useState('');
  const [freq, setFreq] = useState('');
  const [bw, setBw] = useState('');
  const [sf, setSf] = useState('');
  const [cr, setCr] = useState('');
  const [pathHashMode, setPathHashMode] = useState('0');
  const [advertLocationSource, setAdvertLocationSource] = useState<'off' | 'current'>('current');
  const [multiAcksEnabled, setMultiAcksEnabled] = useState(false);
  const [telemetryModeBase, setTelemetryModeBase] = useState(0);
  const [telemetryModeLoc, setTelemetryModeLoc] = useState(0);
  const [telemetryModeEnv, setTelemetryModeEnv] = useState(0);
  const [gettingLocation, setGettingLocation] = useState(false);
  const [mapPickerOpen, setMapPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Identity state
  const [privateKey, setPrivateKey] = useState('');
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityRebooting, setIdentityRebooting] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);

  // Flood & advert control state
  const [advertIntervalHours, setAdvertIntervalHours] = useState('0');
  const [floodScope, setFloodScope] = useState('');
  const [knownRegions, setKnownRegions] = useState('');
  const [maxRadioContacts, setMaxRadioContacts] = useState('');
  const [floodBusy, setFloodBusy] = useState(false);
  const [floodError, setFloodError] = useState<string | null>(null);

  // Advertise state
  const [advertisingMode, setAdvertisingMode] = useState<RadioAdvertMode | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [connectionBusy, setConnectionBusy] = useState(false);

  useEffect(() => {
    if (!config) return;
    setName(config.name);
    setLat(String(config.lat));
    setLon(String(config.lon));
    setTxPower(String(config.tx_power));
    setFreq(String(config.radio.freq));
    setBw(String(config.radio.bw));
    setSf(String(config.radio.sf));
    setCr(String(config.radio.cr));
    setPathHashMode(String(config.path_hash_mode));
    setAdvertLocationSource(config.advert_location_source ?? 'current');
    setMultiAcksEnabled(config.multi_acks_enabled ?? false);
    setTelemetryModeBase(config.telemetry_mode_base ?? 0);
    setTelemetryModeLoc(config.telemetry_mode_loc ?? 0);
    setTelemetryModeEnv(config.telemetry_mode_env ?? 0);
  }, [config]);

  useEffect(() => {
    if (!appSettings) return;
    setAdvertIntervalHours(String(Math.round(appSettings.advert_interval / 3600)));
    setFloodScope(stripRegionScopePrefix(appSettings.flood_scope));
    setKnownRegions((appSettings.known_regions ?? []).join('\n'));
    setMaxRadioContacts(String(appSettings.max_radio_contacts));
  }, [appSettings]);

  // Dirty state per persistence target. Each group's Save covers exactly the fields
  // compared here, so a disabled Save means "nothing of mine has changed" rather
  // than "not available". State is seeded with String(config.x), so comparing the
  // same way is exact.
  const radioConfigDirty = useMemo(() => {
    if (!config) return false;
    return (
      name !== config.name ||
      lat !== String(config.lat) ||
      lon !== String(config.lon) ||
      txPower !== String(config.tx_power) ||
      freq !== String(config.radio.freq) ||
      bw !== String(config.radio.bw) ||
      sf !== String(config.radio.sf) ||
      cr !== String(config.radio.cr) ||
      (config.path_hash_mode_supported && pathHashMode !== String(config.path_hash_mode)) ||
      advertLocationSource !== (config.advert_location_source ?? 'current') ||
      multiAcksEnabled !== (config.multi_acks_enabled ?? false) ||
      telemetryModeBase !== (config.telemetry_mode_base ?? 0) ||
      telemetryModeLoc !== (config.telemetry_mode_loc ?? 0) ||
      telemetryModeEnv !== (config.telemetry_mode_env ?? 0)
    );
  }, [
    config,
    name,
    lat,
    lon,
    txPower,
    freq,
    bw,
    sf,
    cr,
    pathHashMode,
    advertLocationSource,
    multiAcksEnabled,
    telemetryModeBase,
    telemetryModeLoc,
    telemetryModeEnv,
  ]);

  const messagingDirty = useMemo(() => {
    if (!appSettings) return false;
    const parsedRegions = knownRegions
      .split(/[\n,]/)
      .map((r) => r.trim())
      .filter((r) => r.length > 0);
    return (
      floodScope !== stripRegionScopePrefix(appSettings.flood_scope) ||
      JSON.stringify(parsedRegions) !== JSON.stringify(appSettings.known_regions ?? []) ||
      maxRadioContacts !== String(appSettings.max_radio_contacts)
    );
  }, [appSettings, floodScope, knownRegions, maxRadioContacts]);

  const advertIntervalDirty = useMemo(() => {
    if (!appSettings) return false;
    return advertIntervalHours !== String(Math.round(appSettings.advert_interval / 3600));
  }, [appSettings, advertIntervalHours]);

  const resetRadioConfig = () => {
    if (!config) return;
    setError(null);
    setName(config.name);
    setLat(String(config.lat));
    setLon(String(config.lon));
    setTxPower(String(config.tx_power));
    setFreq(String(config.radio.freq));
    setBw(String(config.radio.bw));
    setSf(String(config.radio.sf));
    setCr(String(config.radio.cr));
    setPathHashMode(String(config.path_hash_mode));
    setAdvertLocationSource(config.advert_location_source ?? 'current');
    setMultiAcksEnabled(config.multi_acks_enabled ?? false);
    setTelemetryModeBase(config.telemetry_mode_base ?? 0);
    setTelemetryModeLoc(config.telemetry_mode_loc ?? 0);
    setTelemetryModeEnv(config.telemetry_mode_env ?? 0);
  };

  const resetMessaging = () => {
    if (!appSettings) return;
    setFloodError(null);
    setFloodScope(stripRegionScopePrefix(appSettings.flood_scope));
    setKnownRegions((appSettings.known_regions ?? []).join('\n'));
    setMaxRadioContacts(String(appSettings.max_radio_contacts));
  };

  const currentPreset = useMemo(() => {
    const freqNum = parseFloat(freq);
    const bwNum = parseFloat(bw);
    const sfNum = parseInt(sf, 10);
    const crNum = parseInt(cr, 10);

    for (const preset of RADIO_PRESETS) {
      if (
        preset.freq === freqNum &&
        preset.bw === bwNum &&
        preset.sf === sfNum &&
        preset.cr === crNum
      ) {
        return preset.name;
      }
    }
    return 'custom';
  }, [freq, bw, sf, cr]);

  const handlePresetChange = (presetName: string) => {
    if (presetName === 'custom') return;
    const preset = RADIO_PRESETS.find((p) => p.name === presetName);
    if (preset) {
      setFreq(String(preset.freq));
      setBw(String(preset.bw));
      setSf(String(preset.sf));
      setCr(String(preset.cr));
    }
  };

  const handleGetLocation = () => {
    if (!navigator.geolocation) {
      toast.error(t('settings.radio.geoUnsupported'), {
        description: t('settings.radio.geoUnsupportedHelp'),
      });
      return;
    }

    setGettingLocation(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLat(position.coords.latitude.toFixed(6));
        setLon(position.coords.longitude.toFixed(6));
        setGettingLocation(false);
        toast.success(t('settings.radio.locationUpdated'));
      },
      (err) => {
        setGettingLocation(false);
        toast.error(t('settings.radio.locationFailed'), {
          description: err.message,
        });
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const buildUpdate = (): RadioConfigUpdate | null => {
    const parsedLat = parseFloat(lat);
    const parsedLon = parseFloat(lon);
    const parsedTxPower = parseInt(txPower, 10);
    const parsedFreq = parseFloat(freq);
    const parsedBw = parseFloat(bw);
    const parsedSf = parseInt(sf, 10);
    const parsedCr = parseInt(cr, 10);

    if (
      [parsedLat, parsedLon, parsedTxPower, parsedFreq, parsedBw, parsedSf, parsedCr].some((v) =>
        isNaN(v)
      )
    ) {
      setError(t('settings.radio.numericRequired'));
      return null;
    }

    const parsedPathHashMode = parseInt(pathHashMode, 10);

    return {
      name,
      lat: parsedLat,
      lon: parsedLon,
      tx_power: parsedTxPower,
      ...(advertLocationSource !== (config?.advert_location_source ?? 'current')
        ? { advert_location_source: advertLocationSource }
        : {}),
      ...(multiAcksEnabled !== (config?.multi_acks_enabled ?? false)
        ? { multi_acks_enabled: multiAcksEnabled }
        : {}),
      ...(telemetryModeBase !== (config?.telemetry_mode_base ?? 0)
        ? { telemetry_mode_base: telemetryModeBase }
        : {}),
      ...(telemetryModeLoc !== (config?.telemetry_mode_loc ?? 0)
        ? { telemetry_mode_loc: telemetryModeLoc }
        : {}),
      ...(telemetryModeEnv !== (config?.telemetry_mode_env ?? 0)
        ? { telemetry_mode_env: telemetryModeEnv }
        : {}),
      radio: {
        freq: parsedFreq,
        bw: parsedBw,
        sf: parsedSf,
        cr: parsedCr,
      },
      ...(config?.path_hash_mode_supported &&
      !isNaN(parsedPathHashMode) &&
      parsedPathHashMode !== config.path_hash_mode
        ? { path_hash_mode: parsedPathHashMode }
        : {}),
    };
  };

  const handleSave = async () => {
    setError(null);
    const update = buildUpdate();
    if (!update) return;

    setBusy(true);
    try {
      await onSave(update);
      toast.success(t('settings.radio.configSaved'));
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.radio.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveAndReboot = async () => {
    setError(null);
    const update = buildUpdate();
    if (!update) return;

    setBusy(true);
    try {
      await onSave(update);
      toast.success(t('settings.radio.configSavedRebooting'));
      setRebooting(true);
      await onReboot();
      if (!pageMode) {
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.radio.saveFailed'));
    } finally {
      setRebooting(false);
      setBusy(false);
    }
  };

  const handleSetPrivateKey = async () => {
    if (!privateKey.trim()) {
      setIdentityError(t('settings.radio.privateKeyRequired'));
      return;
    }
    setIdentityError(null);
    setIdentityBusy(true);

    try {
      await onSetPrivateKey(privateKey.trim());
      setPrivateKey('');
      toast.success(t('settings.radio.keySetRebooting'));
      setIdentityRebooting(true);
      await onReboot();
      if (!pageMode) {
        onClose();
      }
    } catch (err) {
      setIdentityError(err instanceof Error ? err.message : t('settings.radio.setKeyFailed'));
    } finally {
      setIdentityRebooting(false);
      setIdentityBusy(false);
    }
  };

  const handleSaveFloodSettings = async () => {
    setFloodError(null);
    setFloodBusy(true);

    try {
      const update: AppSettingsUpdate = {};
      if (!appSettings) {
        return;
      }
      if (floodScope !== stripRegionScopePrefix(appSettings.flood_scope)) {
        update.flood_scope = floodScope;
      }
      // Known regions: one per line (commas also accepted), trimmed, blanks dropped.
      const parsedRegions = knownRegions
        .split(/[\n,]/)
        .map((r) => r.trim())
        .filter((r) => r.length > 0);
      if (JSON.stringify(parsedRegions) !== JSON.stringify(appSettings.known_regions ?? [])) {
        update.known_regions = parsedRegions;
      }
      const newMaxRadioContacts = parseInt(maxRadioContacts, 10);
      if (!isNaN(newMaxRadioContacts) && newMaxRadioContacts !== appSettings.max_radio_contacts) {
        update.max_radio_contacts = newMaxRadioContacts;
      }
      if (Object.keys(update).length > 0) {
        await onSaveAppSettings(update);
      }
      toast.success(t('settings.radio.settingsSaved'));
    } catch (err) {
      setFloodError(err instanceof Error ? err.message : t('settings.radio.saveFailed'));
    } finally {
      setFloodBusy(false);
    }
  };

  const [advertIntervalBusy, setAdvertIntervalBusy] = useState(false);
  const [advertIntervalError, setAdvertIntervalError] = useState<string | null>(null);

  const handleSaveAdvertInterval = async () => {
    setAdvertIntervalError(null);
    setAdvertIntervalBusy(true);

    try {
      if (!appSettings) {
        return;
      }
      const hours = parseInt(advertIntervalHours, 10);
      const newAdvertInterval = isNaN(hours) ? 0 : hours * 3600;
      if (newAdvertInterval !== appSettings.advert_interval) {
        await onSaveAppSettings({ advert_interval: newAdvertInterval });
      }
      toast.success(t('settings.radio.advertIntervalSaved'));
    } catch (err) {
      setAdvertIntervalError(err instanceof Error ? err.message : t('settings.radio.saveFailed'));
    } finally {
      setAdvertIntervalBusy(false);
    }
  };

  const handleAdvertise = async (mode: RadioAdvertMode) => {
    setAdvertisingMode(mode);
    try {
      await onAdvertise(mode);
    } finally {
      setAdvertisingMode(null);
    }
  };

  const handleDiscover = async (target: RadioDiscoveryTarget) => {
    setDiscoverError(null);
    try {
      await onDiscoverMesh(target);
    } catch (err) {
      setDiscoverError(err instanceof Error ? err.message : t('settings.radio.discoverFailed'));
    }
  };

  const handleDiscoverRegions = async () => {
    // Prefer repeaters from the most recent mesh-discovery sweep (they just
    // answered, so they're likely in range for the direct-routed regions
    // request); otherwise let the backend pick recent repeater contacts.
    const discoveredRepeaterKeys = (meshDiscovery?.results ?? [])
      .filter((r) => r.node_type === 'repeater')
      .map((r) => r.public_key);
    await onDiscoverRegions(discoveredRepeaterKeys);
  };

  const handleAddDiscoveredRegions = () => {
    if (!regionDiscovery || regionDiscovery.regions.length === 0) return;
    const existing = knownRegions
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    const seen = new Set(existing.map((s) => s.toLowerCase()));
    const additions = regionDiscovery.regions.filter((r) => !seen.has(r.toLowerCase()));
    if (additions.length === 0) {
      toast.info(t('settings.radio.regionsAlreadyListed'));
      return;
    }
    setKnownRegions([...existing, ...additions].join('\n'));
    toast.success(t('settings.radio.regionsAdded', { count: additions.length }));
  };

  const importInputRef = useRef<HTMLInputElement>(null);
  const [keyImportDialogOpen, setKeyImportDialogOpen] = useState(false);

  // Four screens of scrolling put "send an advertisement" — a frequent, deliberate
  // action — at the very bottom of a page otherwise made of settings people rarely
  // touch. The tabs follow the save boundaries exactly: one tab, one persistence
  // target, one Save. Field state lives here, so switching tabs keeps unsaved edits.
  const [tab, setTab] = useState<string>(config ? 'device' : 'connection');
  const tabChosenRef = useRef(false);
  useEffect(() => {
    if (tabChosenRef.current) return;
    setTab(config ? 'device' : 'connection');
  }, [config]);
  const tabListRef = useRef<HTMLDivElement>(null);
  const selectTab = (next: string) => {
    tabChosenRef.current = true;
    setTab(next);
  };
  useEffect(() => {
    // Keep the selected tab in view when the strip scrolls. Guarded: jsdom has no
    // scrollIntoView, and neither does every embedded browser.
    const active = tabListRef.current?.querySelector('[data-state="active"]');
    active?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [tab]);
  const pendingImportRef = useRef<Record<string, unknown> | null>(null);

  const buildConfigProfile = () => ({
    version: 1,
    exported_at: new Date().toISOString(),
    name: config?.name ?? '',
    lat: config?.lat ?? 0,
    lon: config?.lon ?? 0,
    tx_power: config?.tx_power ?? 0,
    radio: { ...(config?.radio ?? { freq: 0, bw: 0, sf: 0, cr: 0 }) },
    path_hash_mode: config?.path_hash_mode ?? 0,
    advert_location_source: config?.advert_location_source ?? 'current',
    multi_acks_enabled: config?.multi_acks_enabled ?? false,
    telemetry_mode_base: config?.telemetry_mode_base ?? 0,
    telemetry_mode_loc: config?.telemetry_mode_loc ?? 0,
    telemetry_mode_env: config?.telemetry_mode_env ?? 0,
  });

  const downloadJson = (profile: object, suffix: string) => {
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeName = (config?.name || 'radio').replace(/[^a-zA-Z0-9_-]/g, '_');
    const timestamp = new Date()
      .toLocaleString(undefined, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      })
      .replace(/[/:, ]+/g, '-');
    a.download = `${safeName}-${suffix}-${timestamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportConfig = async () => {
    const profile = buildConfigProfile();
    try {
      const { private_key } = await api.getPrivateKey();
      downloadJson({ ...profile, private_key }, 'config');
      toast.success(t('settings.radio.exportWithKey'));
    } catch {
      downloadJson(profile, 'config');
      toast.info(t('settings.radio.exportWithoutKey'), {
        description: t('settings.radio.exportWithoutKeyHelp'),
      });
    }
  };

  const validateImportData = (
    data: unknown
  ): data is {
    name: string;
    radio: { freq: number; bw: number; sf: number; cr: number };
    [k: string]: unknown;
  } =>
    typeof data === 'object' &&
    data !== null &&
    'name' in data &&
    typeof (data as Record<string, unknown>).name === 'string' &&
    'radio' in data &&
    typeof (data as Record<string, unknown>).radio === 'object' &&
    (data as Record<string, unknown>).radio !== null &&
    typeof (data as Record<string, Record<string, unknown>>).radio.freq === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.bw === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.sf === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.cr === 'number';

  const populateFormFromImport = (data: Record<string, unknown>) => {
    const radio = data.radio as { freq: number; bw: number; sf: number; cr: number };
    setName(data.name as string);
    if (typeof data.lat === 'number') setLat(String(data.lat));
    if (typeof data.lon === 'number') setLon(String(data.lon));
    if (typeof data.tx_power === 'number') setTxPower(String(data.tx_power));
    setFreq(String(radio.freq));
    setBw(String(radio.bw));
    setSf(String(radio.sf));
    setCr(String(radio.cr));
    if (typeof data.path_hash_mode === 'number') setPathHashMode(String(data.path_hash_mode));
    if (data.advert_location_source === 'off' || data.advert_location_source === 'current')
      setAdvertLocationSource(data.advert_location_source);
    if (typeof data.multi_acks_enabled === 'boolean') setMultiAcksEnabled(data.multi_acks_enabled);
    if (typeof data.telemetry_mode_base === 'number')
      setTelemetryModeBase(data.telemetry_mode_base);
    if (typeof data.telemetry_mode_loc === 'number') setTelemetryModeLoc(data.telemetry_mode_loc);
    if (typeof data.telemetry_mode_env === 'number') setTelemetryModeEnv(data.telemetry_mode_env);
  };

  const buildUpdateFromImport = (data: Record<string, unknown>): RadioConfigUpdate => {
    const radio = data.radio as { freq: number; bw: number; sf: number; cr: number };
    const update: RadioConfigUpdate = {
      name: data.name as string,
      lat: typeof data.lat === 'number' ? data.lat : (config?.lat ?? 0),
      lon: typeof data.lon === 'number' ? data.lon : (config?.lon ?? 0),
      tx_power:
        typeof data.tx_power === 'number' ? (data.tx_power as number) : (config?.tx_power ?? 0),
      radio,
    };
    if (data.advert_location_source === 'off' || data.advert_location_source === 'current')
      update.advert_location_source = data.advert_location_source;
    if (typeof data.multi_acks_enabled === 'boolean')
      update.multi_acks_enabled = data.multi_acks_enabled;
    if (typeof data.telemetry_mode_base === 'number')
      update.telemetry_mode_base = data.telemetry_mode_base as number;
    if (typeof data.telemetry_mode_loc === 'number')
      update.telemetry_mode_loc = data.telemetry_mode_loc as number;
    if (typeof data.telemetry_mode_env === 'number')
      update.telemetry_mode_env = data.telemetry_mode_env as number;
    if (config?.path_hash_mode_supported && typeof data.path_hash_mode === 'number')
      update.path_hash_mode = data.path_hash_mode as number;
    return update;
  };

  const applyImport = async (data: Record<string, unknown>) => {
    populateFormFromImport(data);
    const update = buildUpdateFromImport(data);

    setBusy(true);
    setRebooting(true);
    try {
      if (typeof data.private_key === 'string' && data.private_key) {
        await onSetPrivateKey(data.private_key);
        toast.success(t('settings.radio.importKeyRebooting'));
      } else {
        toast.success(t('settings.radio.importRebooting'));
      }
      await onSave(update);
      await onReboot();
      if (!pageMode) onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.radio.importFailed'));
    } finally {
      setRebooting(false);
      setBusy(false);
    }
  };

  const handleImportConfig = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);

      if (!validateImportData(data)) {
        toast.error(t('settings.radio.invalidConfig'), {
          description: t('settings.radio.invalidConfigHelp'),
        });
        return;
      }

      if (typeof data.private_key === 'string' && data.private_key) {
        // Private key present — show warning dialog before applying
        pendingImportRef.current = data;
        setKeyImportDialogOpen(true);
      } else {
        await applyImport(data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.radio.importFailed'));
    } finally {
      if (importInputRef.current) importInputRef.current.value = '';
    }
  };

  const handleConfirmKeyImport = async () => {
    setKeyImportDialogOpen(false);
    const data = pendingImportRef.current;
    pendingImportRef.current = null;
    if (data) await applyImport(data);
  };

  const radioState =
    health?.radio_state ?? (health?.radio_initializing ? 'initializing' : 'disconnected');
  const transportConfigured = health?.transport_configured !== false;
  const identityGate = isRadioIdentityGate(radioState);
  const connectionActionLabel =
    radioState === 'paused'
      ? t('settings.radio.reconnect')
      : radioState === 'connected' || radioState === 'initializing'
        ? t('settings.radio.disconnect')
        : t('settings.radio.stopTrying');

  const connectionStatusLabel =
    radioState === 'connected'
      ? health?.connection_info || t('settings.radio.connected')
      : radioState === 'initializing'
        ? t('settings.radio.initializing', {
            info: health?.connection_info || t('settings.radio.radioNoun'),
          })
        : radioState === 'connecting'
          ? health?.connection_info
            ? t('settings.radio.connectingTo', { info: health.connection_info })
            : t('settings.radio.connecting')
          : radioState === 'paused'
            ? health?.connection_info
              ? t('settings.radio.pausedWith', { info: health.connection_info })
              : t('settings.radio.paused')
            : radioState === 'identity_mismatch'
              ? t('settings.radio.identityMismatch')
              : radioState === 'identity_unbound_legacy'
                ? t('settings.radio.identityUnbound')
                : t('settings.radio.notConnected');

  const deviceInfoLabel = useMemo(() => {
    const info = health?.radio_device_info;
    if (!info) {
      return null;
    }

    const model = info.model?.trim() || null;
    const firmwareParts = [info.firmware_build?.trim(), info.firmware_version?.trim()].filter(
      (value): value is string => Boolean(value)
    );
    const capacityParts = [
      typeof info.max_contacts === 'number'
        ? t('settings.radio.contactsCount', { count: info.max_contacts })
        : null,
      typeof info.max_channels === 'number'
        ? t('settings.radio.channelsCount', { count: info.max_channels })
        : null,
    ].filter((value): value is string => value !== null);

    if (!model && firmwareParts.length === 0 && capacityParts.length === 0) {
      return null;
    }

    let label = model ?? t('settings.radio.deviceFallback');
    if (firmwareParts.length > 0) {
      label = t('settings.radio.deviceRunning', {
        model: label,
        firmware: firmwareParts.join('/'),
      });
    }
    if (capacityParts.length > 0) {
      label += t('settings.radio.deviceMax', { capacity: capacityParts.join(', ') });
    }
    return label;
  }, [health?.radio_device_info, t]);

  const handleConnectionAction = async () => {
    if (identityGate) {
      return;
    }
    if (radioState === 'paused' && !transportConfigured) {
      toast.info(t('settings.radio.saveTransportFirst'));
      return;
    }
    setConnectionBusy(true);
    try {
      if (radioState === 'paused') {
        await onReconnect();
        toast.success(t('settings.radio.reconnectRequested'));
      } else {
        await onDisconnect();
        toast.success(t('settings.radio.connectionPaused'));
      }
    } catch (err) {
      toast.error(t('settings.radio.connectionChangeFailed'), {
        description:
          err instanceof Error ? err.message : t('settings.radio.connectionChangeFailedHelp'),
      });
    } finally {
      setConnectionBusy(false);
    }
  };

  return (
    <div className={className}>
      <Tabs value={tab} onValueChange={selectTab} className="space-y-4">
        {/* A single row that scrolls when the labels do not fit. TabsList is a fixed
            40px tall, so wrapping the triggers onto a second row pushed them outside
            their own container: on a phone the last two tabs floated unstyled over
            the content below. Scrolling keeps one row at any width or language. */}
        <TabsList
          ref={tabListRef}
          className="flex w-full justify-start gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          <TabsTrigger value="connection" className="shrink-0">
            {t('settings.radio.tabConnection')}
          </TabsTrigger>
          <TabsTrigger value="device" disabled={!config} className="shrink-0">
            {t('settings.radio.tabDevice')}
            {radioConfigDirty && <DirtyDot />}
          </TabsTrigger>
          <TabsTrigger value="messaging" disabled={!config || !appSettings} className="shrink-0">
            {t('settings.radio.tabMessaging')}
            {messagingDirty && <DirtyDot />}
          </TabsTrigger>
          <TabsTrigger value="advert" disabled={!config || !appSettings} className="shrink-0">
            {t('settings.radio.tabAdvert')}
            {advertIntervalDirty && <DirtyDot />}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="connection" className="m-0 space-y-4">
          {/* ── Connection ── */}
          <div className="space-y-3">
            <h3 className="text-base font-semibold tracking-tight">
              {t('settings.radio.connection')}
            </h3>
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  radioState === 'connected'
                    ? 'bg-status-connected'
                    : radioState === 'initializing' ||
                        radioState === 'connecting' ||
                        isRadioIdentityGate(radioState)
                      ? 'bg-warning'
                      : 'bg-status-disconnected'
                }`}
              />
              <span
                className={
                  radioState === 'paused' || radioState === 'disconnected'
                    ? 'text-muted-foreground'
                    : ''
                }
              >
                {connectionStatusLabel}
              </span>
            </div>
            {deviceInfoLabel && <p className="text-sm text-muted-foreground">{deviceInfoLabel}</p>}

            {health?.radio_stats && <RadioDetailsCollapsible stats={health.radio_stats} />}

            <RadioTransportPanel health={health} onHealthRefresh={onHealthRefresh} />

            <Button
              type="button"
              variant="outline"
              onClick={handleConnectionAction}
              disabled={
                connectionBusy || identityGate || (radioState === 'paused' && !transportConfigured)
              }
              className="w-full"
            >
              {connectionBusy
                ? t('settings.radio.busySuffix', { label: connectionActionLabel })
                : connectionActionLabel}
            </Button>
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.disconnectHelp')}
            </p>
          </div>

          {!config || !appSettings ? (
            <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.unavailableUntilConnected')}
            </p>
          ) : null}
        </TabsContent>

        {config && appSettings ? (
          <>
            <TabsContent value="device" className="m-0 space-y-4">
              {/* Everything from here to the action row below is written by one call to
              PATCH /api/radio/config. Keeping those fields inside a single titled
              group is the point: previously the radio name sat three screens above
              the button that saved it, and the multi-ACK checkbox sat below it under
              a different heading. */}
              <SettingsGroup id="radio-device-config">
                <SettingsGroupHeader
                  id="radio-device-config"
                  title={t('settings.radio.deviceConfig')}
                  storedOn="radio"
                  dirty={radioConfigDirty}
                />

                {/* ── Identity ── */}
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">{t('settings.radio.identity')}</h4>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="name">{t('settings.radio.radioName')}</Label>
                  <Input
                    id="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="max-w-md"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="public-key">{t('settings.radio.publicKey')}</Label>
                  <Input
                    id="public-key"
                    value={config.public_key}
                    disabled
                    className="font-mono text-xs"
                  />
                </div>

                {/* ── Radio Parameters ── */}
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">{t('settings.radio.parameters')}</h4>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="preset">{t('settings.radio.preset')}</Label>
                  <select
                    id="preset"
                    value={currentPreset}
                    onChange={(e) => handlePresetChange(e.target.value)}
                    className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                  >
                    <option value="custom">{t('settings.radio.custom')}</option>
                    {RADIO_PRESETS.map((preset) => (
                      <option key={preset.name} value={preset.name}>
                        {preset.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="freq">{t('settings.radio.frequency')}</Label>
                    <Input
                      id="freq"
                      type="number"
                      step="any"
                      value={freq}
                      onChange={(e) => setFreq(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bw">{t('settings.radio.bandwidth')}</Label>
                    <Input
                      id="bw"
                      type="number"
                      step="any"
                      value={bw}
                      onChange={(e) => setBw(e.target.value)}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="sf">{t('settings.radio.spreadingFactor')}</Label>
                    <Input
                      id="sf"
                      type="number"
                      min="7"
                      max="12"
                      value={sf}
                      onChange={(e) => setSf(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="cr">{t('settings.radio.codingRate')}</Label>
                    <Input
                      id="cr"
                      type="number"
                      min="5"
                      max="8"
                      value={cr}
                      onChange={(e) => setCr(e.target.value)}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="tx-power">{t('settings.radio.txPower')}</Label>
                    <Input
                      id="tx-power"
                      type="number"
                      value={txPower}
                      onChange={(e) => setTxPower(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-tx">{t('settings.radio.maxTxPower')}</Label>
                    <Input id="max-tx" type="number" value={config.max_tx_power} disabled />
                  </div>
                </div>

                {config.path_hash_mode_supported && (
                  <SettingsField help={t('settings.radio.pathHashHelp')}>
                    <Label htmlFor="path-hash-mode">{t('settings.radio.pathHashMode')}</Label>
                    <select
                      id="path-hash-mode"
                      value={pathHashMode}
                      onChange={(e) => setPathHashMode(e.target.value)}
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                    >
                      <option value="0">{t('settings.radio.pathHash1')}</option>
                      <option value="1">{t('settings.radio.pathHash2')}</option>
                      <option value="2">{t('settings.radio.pathHash3')}</option>
                    </select>
                  </SettingsField>
                )}

                {/* ── Location ── */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">{t('settings.radio.location')}</h4>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          if (!canShareRadioLocation(config.lat, config.lon)) {
                            toast.error(t('share.noLocation'));
                            return;
                          }
                          void navigator.clipboard
                            .writeText(`${config.lat.toFixed(5)}, ${config.lon.toFixed(5)}`)
                            .then(() => {
                              toast.success(t('share.locationCopied'));
                            });
                        }}
                        disabled={!canShareRadioLocation(config.lat, config.lon)}
                      >
                        <Share2 className="mr-1.5 h-4 w-4" aria-hidden="true" />
                        {t('share.shareLocation')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={handleGetLocation}
                        disabled={gettingLocation}
                      >
                        {gettingLocation ? (
                          t('settings.radio.getting')
                        ) : (
                          <>
                            <MapPinned className="mr-1.5 h-4 w-4" aria-hidden="true" />
                            {t('settings.radio.useMyLocation')}
                          </>
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setMapPickerOpen(true)}
                      >
                        <Map className="mr-1.5 h-4 w-4" aria-hidden="true" />
                        {t('settings.radio.pickOnMap')}
                      </Button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="lat" className="text-xs text-muted-foreground">
                        {t('settings.radio.latitude')}
                      </Label>
                      <Input
                        id="lat"
                        type="number"
                        step="any"
                        value={lat}
                        onChange={(e) => setLat(e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="lon" className="text-xs text-muted-foreground">
                        {t('settings.radio.longitude')}
                      </Label>
                      <Input
                        id="lon"
                        type="number"
                        step="any"
                        value={lon}
                        onChange={(e) => setLon(e.target.value)}
                      />
                    </div>
                  </div>
                  <SettingsField help={t('settings.radio.advertLocationHelp')}>
                    <Label htmlFor="advert-location-source">
                      {t('settings.radio.advertLocationSource')}
                    </Label>
                    <select
                      id="advert-location-source"
                      value={advertLocationSource}
                      onChange={(e) => setAdvertLocationSource(e.target.value as 'off' | 'current')}
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                    >
                      <option value="off">{t('settings.radio.advertOff')}</option>
                      <option value="current">{t('settings.radio.includeLocation')}</option>
                    </select>
                  </SettingsField>
                </div>

                {/* ── Telemetry Sharing ── */}
                <div className="space-y-3">
                  <div className="gap-x-8 lg:grid lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-baseline">
                    <h4 className="text-sm font-semibold">
                      {t('settings.radio.telemetrySharing')}
                    </h4>
                    <p className="mt-2 text-[0.8125rem] text-muted-foreground lg:mt-0">
                      {t('settings.radio.telemetrySharingHelp')}
                    </p>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="telemetry-mode-base" className="text-sm">
                        {t('settings.radio.batteryBase')}
                      </Label>
                      <select
                        id="telemetry-mode-base"
                        value={telemetryModeBase}
                        onChange={(e) => setTelemetryModeBase(Number(e.target.value))}
                        className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                      >
                        <option value={0}>{t('settings.radio.deny')}</option>
                        <option value={1}>{t('settings.radio.perContact')}</option>
                        <option value={2}>{t('settings.radio.allowAll')}</option>
                      </select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="telemetry-mode-loc" className="text-sm">
                        {t('settings.radio.locationMode')}
                      </Label>
                      <select
                        id="telemetry-mode-loc"
                        value={telemetryModeLoc}
                        onChange={(e) => setTelemetryModeLoc(Number(e.target.value))}
                        className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                      >
                        <option value={0}>{t('settings.radio.deny')}</option>
                        <option value={1}>{t('settings.radio.perContact')}</option>
                        <option value={2}>{t('settings.radio.allowAll')}</option>
                      </select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="telemetry-mode-env" className="text-sm">
                        {t('settings.radio.envSensors')}
                      </Label>
                      <select
                        id="telemetry-mode-env"
                        value={telemetryModeEnv}
                        onChange={(e) => setTelemetryModeEnv(Number(e.target.value))}
                        className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                      >
                        <option value={0}>{t('settings.radio.deny')}</option>
                        <option value={1}>{t('settings.radio.perContact')}</option>
                        <option value={2}>{t('settings.radio.allowAll')}</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* ── Direct ACKs ── (written by the same PATCH as everything above) */}
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">{t('settings.radio.messaging')}</h4>
                  <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
                    <Checkbox
                      id="multi-acks-enabled"
                      checked={multiAcksEnabled}
                      onCheckedChange={(checked) => setMultiAcksEnabled(checked === true)}
                      className="mt-0.5"
                    />
                    <div className="space-y-1">
                      <Label htmlFor="multi-acks-enabled">{t('settings.radio.multiAcks')}</Label>
                      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                        {t('settings.radio.multiAcksHelp')}
                      </p>
                    </div>
                  </div>
                </div>

                {error && (
                  <div className="text-sm text-destructive" role="alert">
                    {error}
                  </div>
                )}

                {/* Save writes the group to the radio. Save and reboot does the same and
              then restarts it, because some firmware only picks certain settings up
              on boot — two genuinely different operations, so the reboot one is the
              secondary action rather than the eye-catching one. */}
                {/* This group runs about two screens tall, so while something is unsaved
                the action row follows the scroll. With nothing to save it stays in
                flow rather than parking a bar over the content. */}
                <div
                  className={cn(
                    'flex flex-col items-stretch gap-2 border-t border-border pt-3',
                    'sm:flex-row sm:flex-wrap sm:items-center',
                    radioConfigDirty &&
                      'sticky bottom-0 z-10 -mx-4 bg-background px-4 pb-3 shadow-[0_-10px_20px_-14px_hsl(var(--overlay))]'
                  )}
                >
                  <Button
                    onClick={handleSave}
                    disabled={busy || rebooting || !radioConfigDirty}
                    className="sm:w-auto"
                  >
                    {busy && !rebooting
                      ? t('settings.radio.saving')
                      : t('settings.radio.saveConfig')}
                  </Button>
                  <Button
                    onClick={handleSaveAndReboot}
                    disabled={busy || rebooting || !radioConfigDirty}
                    variant="outline"
                  >
                    {rebooting
                      ? t('settings.radio.rebooting')
                      : t('settings.radio.saveConfigReboot')}
                  </Button>
                  {radioConfigDirty && (
                    <Button onClick={resetRadioConfig} disabled={busy || rebooting} variant="ghost">
                      {t('settings.radio.revert')}
                    </Button>
                  )}
                  <span className="text-[0.8125rem] text-muted-foreground sm:ml-auto">
                    {radioConfigDirty
                      ? t('settings.radio.unsavedHint')
                      : t('settings.radio.everythingSaved')}
                  </span>
                </div>
                <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                  {t('settings.radio.rebootHelp')}
                </p>
              </SettingsGroup>

              <Separator />

              {/* ── Config file ── */}
              <AdvancedBlock title={t('settings.radio.configFile')}>
                {/* Two long labels side by side pushed the second button past the
                    right edge at 390px. They stack until there is room for both. */}
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleExportConfig}
                    className="flex-1"
                  >
                    <Download className="mr-1.5 h-4 w-4" aria-hidden="true" />
                    {t('settings.radio.exportConfig')}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => importInputRef.current?.click()}
                    disabled={busy || rebooting}
                    className="flex-1"
                  >
                    <Upload className="mr-1.5 h-4 w-4" aria-hidden="true" />
                    {t('settings.radio.importReboot')}
                  </Button>
                  <input
                    ref={importInputRef}
                    type="file"
                    accept=".json"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleImportConfig(file);
                    }}
                  />
                </div>
                <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                  {t('settings.radio.exportHelp')}
                </p>
              </AdvancedBlock>

              {/* ── Private key ── (its own endpoint, and it always reboots the radio) */}
              <AdvancedBlock title={t('settings.radio.privateKeyGroup')}>
                <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                  {t('settings.radio.storedOnRadio')}
                </p>
                <div className="space-y-2">
                  <Label htmlFor="private-key">{t('settings.radio.setPrivateKey')}</Label>
                  <Input
                    id="private-key"
                    type="password"
                    autoComplete="off"
                    value={privateKey}
                    onChange={(e) => setPrivateKey(e.target.value)}
                    placeholder={t('settings.radio.privateKeyPlaceholder')}
                  />
                  <Button
                    onClick={handleSetPrivateKey}
                    disabled={identityBusy || identityRebooting || !privateKey.trim()}
                    className="w-full border-destructive/50 text-destructive hover:bg-destructive/10"
                    variant="outline"
                  >
                    {identityBusy || identityRebooting
                      ? t('settings.radio.settingRebooting')
                      : t('settings.radio.setKeyReboot')}
                  </Button>
                </div>

                {identityError && (
                  <div className="text-sm text-destructive" role="alert">
                    {identityError}
                  </div>
                )}
              </AdvancedBlock>
            </TabsContent>

            <TabsContent value="messaging" className="m-0 space-y-4">
              {/* ── Meshloom-side messaging settings ── (PATCH /api/settings, never the radio) */}
              <SettingsGroup id="radio-app-messaging">
                <SettingsGroupHeader
                  id="radio-app-messaging"
                  title={t('settings.radio.messaging')}
                  storedOn="server"
                  dirty={messagingDirty}
                />

                <div className="space-y-2">
                  <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
                    <Checkbox
                      id="auto-resend-channel"
                      checked={appSettings.auto_resend_channel}
                      onCheckedChange={(checked) =>
                        onSaveAppSettings({ auto_resend_channel: checked === true })
                      }
                      className="mt-0.5"
                    />
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <Label htmlFor="auto-resend-channel">
                          {t('settings.radio.autoResend')}
                        </Label>
                        <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                          {t('settings.radio.appliesImmediately')}
                        </span>
                      </div>
                      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                        {t('settings.radio.autoResendHelp')}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="flood-scope">{t('settings.radio.floodScope')}</Label>
                  <Input
                    id="flood-scope"
                    className="max-w-sm"
                    value={floodScope}
                    onChange={(e) => setFloodScope(e.target.value)}
                    placeholder="MyRegion"
                  />
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.floodScopeHelp')}
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="known-regions">{t('settings.radio.knownRegions')}</Label>
                  <textarea
                    id="known-regions"
                    value={knownRegions}
                    onChange={(e) => setKnownRegions(e.target.value)}
                    rows={4}
                    placeholder={'nl-gr\nde-by\nMyRegion'}
                    spellCheck={false}
                    className="flex w-full max-w-md rounded-md border border-input bg-background px-3 py-2 font-mono text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  />
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.knownRegionsHelp')}
                  </p>

                  <div className="space-y-2 rounded-md border border-input bg-muted/20 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                        {t('settings.radio.discoverRegionsTitle')}
                      </span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={handleDiscoverRegions}
                        disabled={regionDiscoveryLoading || !health?.radio_connected}
                      >
                        {regionDiscoveryLoading
                          ? t('settings.radio.askingRepeaters')
                          : t('settings.radio.discoverRegions')}
                      </Button>
                    </div>
                    <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                      {t('settings.radio.discoverRegionsHelp')}
                    </p>
                    {!health?.radio_connected && (
                      <p className="text-sm text-destructive">{t('chat.radioNotConnected')}</p>
                    )}
                    {regionDiscovery && (
                      <div className="space-y-2">
                        <p className="text-sm font-medium">
                          {t('settings.radio.regionsAnswered', {
                            answered: regionDiscovery.repeaters_answered,
                            queried: regionDiscovery.repeaters_queried,
                            count: regionDiscovery.repeaters_queried,
                          })}
                          {regionDiscovery.regions.length > 0
                            ? t('settings.radio.regionsFound', {
                                count: regionDiscovery.regions.length,
                              })
                            : ''}
                        </p>
                        {regionDiscovery.regions.length > 0 ? (
                          <>
                            <div className="flex flex-wrap gap-1.5">
                              {regionDiscovery.regions.map((region) => (
                                <span
                                  key={region}
                                  className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 font-mono"
                                >
                                  {region}
                                </span>
                              ))}
                            </div>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={handleAddDiscoveredRegions}
                              className="border-success/50 text-success hover:bg-success/10"
                            >
                              {t('settings.radio.addToKnown')}
                            </Button>
                          </>
                        ) : (
                          regionDiscovery.repeaters_queried > 0 && (
                            <p className="text-sm text-muted-foreground">
                              {t('settings.radio.noRegionsReported')}
                            </p>
                          )
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="max-contacts">{t('settings.radio.maxContacts')}</Label>
                  <Input
                    id="max-contacts"
                    type="number"
                    min="1"
                    max="1000"
                    value={maxRadioContacts}
                    onChange={(e) => setMaxRadioContacts(e.target.value)}
                    className="max-w-[8rem]"
                  />
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.maxContactsHelp')}
                  </p>
                  {health?.radio_device_info?.max_contacts != null &&
                    Number(maxRadioContacts) > health.radio_device_info.max_contacts && (
                      <p className="text-xs text-warning">
                        {t('settings.radio.maxContactsWarn', {
                          max: health.radio_device_info.max_contacts,
                        })}
                      </p>
                    )}
                </div>

                {floodError && (
                  <div className="text-sm text-destructive" role="alert">
                    {floodError}
                  </div>
                )}

                <div
                  className={cn(
                    'flex flex-col items-stretch gap-2 border-t border-border pt-3',
                    'sm:flex-row sm:flex-wrap sm:items-center',
                    messagingDirty &&
                      'sticky bottom-0 z-10 -mx-4 bg-background px-4 pb-3 shadow-[0_-10px_20px_-14px_hsl(var(--overlay))]'
                  )}
                >
                  <Button onClick={handleSaveFloodSettings} disabled={floodBusy || !messagingDirty}>
                    {floodBusy ? t('settings.radio.saving') : t('settings.radio.saveMessaging')}
                  </Button>
                  {messagingDirty && (
                    <Button onClick={resetMessaging} disabled={floodBusy} variant="ghost">
                      {t('settings.radio.revert')}
                    </Button>
                  )}
                  <span className="text-[0.8125rem] text-muted-foreground sm:ml-auto">
                    {messagingDirty
                      ? t('settings.radio.unsavedHint')
                      : t('settings.radio.everythingSaved')}
                  </span>
                </div>
              </SettingsGroup>
            </TabsContent>

            <TabsContent value="advert" className="m-0 space-y-4">
              {/* ── Advertising & Discovery ── */}
              <div className="space-y-5">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <h3 className="text-base font-semibold tracking-tight">
                    {t('settings.radio.advertDiscovery')}
                  </h3>
                  <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                    {t('settings.radio.storedInMeshloom')}
                  </span>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="advert-interval">{t('settings.radio.advertInterval')}</Label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="advert-interval"
                      type="number"
                      min="0"
                      value={advertIntervalHours}
                      onChange={(e) => setAdvertIntervalHours(e.target.value)}
                      className="w-28"
                    />
                    <span className="text-sm text-muted-foreground">
                      {t('settings.radio.hoursOff')}
                    </span>
                  </div>
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.advertIntervalHelp')}
                  </p>
                  {advertIntervalError && (
                    <div className="text-sm text-destructive" role="alert">
                      {advertIntervalError}
                    </div>
                  )}
                  <Button
                    onClick={handleSaveAdvertInterval}
                    disabled={advertIntervalBusy || !advertIntervalDirty}
                  >
                    {advertIntervalBusy
                      ? t('settings.radio.saving')
                      : t('settings.radio.saveAdvertInterval')}
                  </Button>
                </div>

                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">{t('settings.radio.sendAdvert')}</h4>
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.sendAdvertHelp')}
                  </p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <Button
                      onClick={() => handleAdvertise('flood')}
                      disabled={advertisingMode !== null || !health?.radio_connected}
                      className="w-full bg-warning hover:bg-warning/90 text-warning-foreground"
                    >
                      {advertisingMode === 'flood'
                        ? t('settings.radio.sending')
                        : t('settings.radio.sendFlood')}
                    </Button>
                    <Button
                      onClick={() => handleAdvertise('zero_hop')}
                      disabled={advertisingMode !== null || !health?.radio_connected}
                      className="w-full"
                    >
                      {advertisingMode === 'zero_hop'
                        ? t('settings.radio.sending')
                        : t('settings.radio.sendZeroHop')}
                    </Button>
                  </div>
                  {!health?.radio_connected && (
                    <p className="text-sm text-destructive">{t('chat.radioNotConnected')}</p>
                  )}
                </div>

                <div className="space-y-3">
                  <h4 className="text-sm font-semibold">{t('settings.radio.meshDiscovery')}</h4>
                  <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                    {t('settings.radio.meshDiscoveryHelp')}
                  </p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    {[
                      { target: 'repeaters', label: t('settings.radio.discoverRepeaters') },
                      { target: 'sensors', label: t('settings.radio.discoverSensors') },
                      { target: 'all', label: t('settings.radio.discoverBoth') },
                    ].map(({ target, label }) => (
                      <Button
                        key={target}
                        type="button"
                        variant="outline"
                        onClick={() => handleDiscover(target as RadioDiscoveryTarget)}
                        disabled={meshDiscoveryLoadingTarget !== null || !health?.radio_connected}
                        className="w-full"
                      >
                        {meshDiscoveryLoadingTarget === target
                          ? t('settings.radio.listening')
                          : label}
                      </Button>
                    ))}
                  </div>
                  {!health?.radio_connected && (
                    <p className="text-sm text-destructive">{t('chat.radioNotConnected')}</p>
                  )}
                  {discoverError && (
                    <p className="text-sm text-destructive" role="alert">
                      {discoverError}
                    </p>
                  )}
                  {meshDiscovery && (
                    <div className="space-y-2 rounded-md border border-input bg-muted/20 p-3">
                      <div className="flex items-center justify-between gap-4">
                        <p className="text-sm font-medium">
                          {t('settings.radio.lastSweep', { count: meshDiscovery.results.length })}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {t('settings.radio.listenWindow', {
                            seconds: meshDiscovery.duration_seconds.toFixed(0),
                          })}
                        </p>
                      </div>
                      {meshDiscovery.results.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          {t('settings.radio.noNodesResponded')}
                        </p>
                      ) : (
                        <div className="space-y-2">
                          {meshDiscovery.results.map((result) => (
                            <div
                              key={result.public_key}
                              className="rounded-md border border-input bg-background px-3 py-2"
                            >
                              <div className="flex items-center justify-between gap-3">
                                <span className="text-sm font-medium">
                                  {result.name ?? (
                                    <span className="capitalize">{result.node_type}</span>
                                  )}
                                </span>
                                <span className="text-xs text-muted-foreground">
                                  {t('settings.radio.heardCount', { count: result.heard_count })}
                                </span>
                              </div>
                              {result.name && (
                                <p className="text-xs capitalize text-muted-foreground">
                                  {result.node_type}
                                </p>
                              )}
                              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                                {result.public_key}
                              </p>
                              <p className="mt-1 text-xs text-muted-foreground">
                                {t('settings.radio.heardHere', {
                                  snr: result.local_snr ?? t('settings.radio.na'),
                                  rssi: result.local_rssi ?? t('settings.radio.na'),
                                  remote: result.remote_snr ?? t('settings.radio.na'),
                                })}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </TabsContent>

            {/* ── Private Key Import Warning ── */}
            {mapPickerOpen && (
              <Suspense fallback={null}>
                <LocationPickerModal
                  open={mapPickerOpen}
                  onOpenChange={setMapPickerOpen}
                  initialLat={lat}
                  initialLon={lon}
                  onApply={(nextLat, nextLon) => {
                    setLat(formatPickedCoordinate(nextLat));
                    setLon(formatPickedCoordinate(nextLon));
                    toast.success(t('settings.radio.locationUpdated'));
                  }}
                />
              </Suspense>
            )}
            <Dialog
              open={keyImportDialogOpen}
              onOpenChange={(open) => {
                setKeyImportDialogOpen(open);
                if (!open) pendingImportRef.current = null;
              }}
            >
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>{t('settings.radio.importKeyTitle')}</DialogTitle>
                  <DialogDescription>{t('settings.radio.importKeyBody')}</DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setKeyImportDialogOpen(false);
                      pendingImportRef.current = null;
                    }}
                  >
                    {t('settings.radio.cancel')}
                  </Button>
                  <Button
                    onClick={handleConfirmKeyImport}
                    className="border-destructive/50 text-destructive hover:bg-destructive/10"
                    variant="outline"
                  >
                    {t('settings.radio.importConfigKey')}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        ) : null}
      </Tabs>
    </div>
  );
}
