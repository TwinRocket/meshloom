interface RadioSettings {
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export type RadioTransportKind = 'serial' | 'tcp' | 'ble';

export type RadioIdentityGateState = 'identity_mismatch' | 'identity_unbound_legacy';

export function isRadioIdentityGate(
  state: string | null | undefined
): state is RadioIdentityGateState {
  return state === 'identity_mismatch' || state === 'identity_unbound_legacy';
}

export function publicKeyPrefix(key: string | null | undefined): string {
  if (!key) return '';
  return key.slice(0, 12).toLowerCase();
}

export interface RadioSerialPortInfo {
  path: string;
  description: string;
}

export interface RadioBleDeviceInfo {
  address: string;
  name: string | null;
}

export interface RadioTransportCapabilities {
  tcp: boolean;
  serial: boolean;
  ble: boolean;
  serial_unavailable_reason: string | null;
  ble_unavailable_reason: string | null;
}

export interface RadioTransportConfig {
  configured: boolean;
  transport: RadioTransportKind | null;
  serial_port: string;
  serial_baudrate: number;
  tcp_host: string;
  tcp_port: number;
  ble_address: string;
  ble_pin_configured: boolean;
  bound_public_key: string | null;
  capabilities: RadioTransportCapabilities;
  serial_ports: RadioSerialPortInfo[];
}

export interface RadioProxyStatus {
  /** The host decides the listening port; the field says so rather than accepting
   *  a value that cannot take effect. */
  port_managed_by_host?: boolean;
  enabled: boolean;
  bind: string;
  port: number;
  max_clients: number;
  listening: boolean;
  client_count: number;
  dropped_messages: number;
  dropped_logs: number;
  last_error: string | null;
  instance_id: string;
  model: string;
}

export interface RadioProxyUpdate {
  enabled?: boolean;
  bind?: string;
  port?: number;
  max_clients?: number;
}

export interface RadioTransportUpdate {
  transport: RadioTransportKind;
  serial_port?: string | null;
  serial_baudrate?: number | null;
  tcp_host?: string | null;
  tcp_port?: number | null;
  ble_address?: string | null;
  ble_pin?: string | null;
}

export interface RadioIdentityInfo {
  previous_public_key: string | null;
  new_public_key: string | null;
  new_name: string | null;
  mesh_contacts: number;
  mesh_messages: number;
  last_activity: number | null;
}

export interface RadioIdentityActionResponse {
  status: string;
  radio_state:
    | 'connected'
    | 'initializing'
    | 'connecting'
    | 'disconnected'
    | 'paused'
    | 'identity_mismatch'
    | 'identity_unbound_legacy';
  bound_public_key: string | null;
  connected: boolean;
}

export interface RadioConfig {
  public_key: string;
  name: string;
  lat: number;
  lon: number;
  tx_power: number;
  max_tx_power: number;
  radio: RadioSettings;
  path_hash_mode: number;
  path_hash_mode_supported: boolean;
  advert_location_source?: 'off' | 'current';
  multi_acks_enabled?: boolean;
  telemetry_mode_base?: number;
  telemetry_mode_loc?: number;
  telemetry_mode_env?: number;
}

export interface RadioConfigUpdate {
  name?: string;
  lat?: number;
  lon?: number;
  tx_power?: number;
  radio?: RadioSettings;
  path_hash_mode?: number;
  advert_location_source?: 'off' | 'current';
  multi_acks_enabled?: boolean;
  telemetry_mode_base?: number;
  telemetry_mode_loc?: number;
  telemetry_mode_env?: number;
}

export type RadioDiscoveryTarget = 'repeaters' | 'sensors' | 'all';

export interface RadioDiscoveryResult {
  public_key: string;
  name: string | null;
  node_type: 'repeater' | 'sensor';
  heard_count: number;
  local_snr: number | null;
  local_rssi: number | null;
  remote_snr: number | null;
}

export interface RadioDiscoveryResponse {
  target: RadioDiscoveryTarget;
  duration_seconds: number;
  results: RadioDiscoveryResult[];
}

export interface RadioRegionDiscoveryRepeater {
  public_key: string;
  name: string | null;
  answered: boolean;
  regions: string[];
}

export interface RadioRegionDiscoveryResponse {
  repeaters_queried: number;
  repeaters_answered: number;
  /** Deduplicated union of flood-allowed region names across all repeaters. */
  regions: string[];
  results: RadioRegionDiscoveryRepeater[];
}

export type RadioAdvertMode = 'flood' | 'zero_hop';

export interface FanoutStatusEntry {
  name: string;
  type: string;
  status: string;
  last_error?: string | null;
}

export interface AppInfo {
  version: string;
  commit_hash: string | null;
}

export interface RadioStatsSnapshot {
  timestamp: number | null;
  battery_mv: number | null;
  uptime_secs: number | null;
  queue_len: number | null;
  errors: number | null;
  noise_floor: number | null;
  last_rssi: number | null;
  last_snr: number | null;
  tx_air_secs: number | null;
  rx_air_secs: number | null;
  packets_recv: number | null;
  packets_sent: number | null;
  flood_tx: number | null;
  direct_tx: number | null;
  flood_rx: number | null;
  direct_rx: number | null;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  radio_initializing: boolean;
  radio_state?:
    | 'connected'
    | 'initializing'
    | 'connecting'
    | 'disconnected'
    | 'paused'
    | 'identity_mismatch'
    | 'identity_unbound_legacy';
  connection_info: string | null;
  transport_configured?: boolean;
  identity?: RadioIdentityInfo | null;
  app_info?: AppInfo | null;
  radio_device_info?: {
    model: string | null;
    firmware_build: string | null;
    firmware_version: string | null;
    max_contacts: number | null;
    max_channels: number | null;
  } | null;
  radio_stats?: RadioStatsSnapshot | null;
  database_size_mb: number;
  oldest_undecrypted_timestamp: number | null;
  fanout_statuses: Record<string, FanoutStatusEntry>;
  bots_disabled: boolean;
  bots_disabled_source?: 'env' | 'until_restart' | null;
  basic_auth_enabled?: boolean;
  radio_proxy?: RadioProxyStatus | null;
}

export interface FanoutConfig {
  id: string;
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  sort_order: number;
  created_at: number;
}

export interface MaintenanceResult {
  packets_deleted: number;
  vacuumed: boolean;
}

export interface Contact {
  public_key: string;
  name: string | null;
  type: number;
  flags: number;
  direct_path: string | null;
  direct_path_len: number;
  direct_path_hash_mode: number;
  direct_path_updated_at?: number | null;
  route_override_path?: string | null;
  route_override_len?: number | null;
  route_override_hash_mode?: number | null;
  effective_route?: ContactRoute | null;
  effective_route_source?: 'override' | 'direct' | 'flood';
  direct_route?: ContactRoute | null;
  route_override?: ContactRoute | null;
  last_advert: number | null;
  lat: number | null;
  lon: number | null;
  last_seen: number | null;
  on_radio: boolean;
  favorite: boolean;
  last_contacted: number | null;
  last_read_at: number | null;
  first_seen: number | null;
}

export interface ContactRoute {
  path: string;
  path_len: number;
  path_hash_mode: number;
}

export interface ContactAdvertPath {
  path: string;
  path_len: number;
  next_hop: string | null;
  first_seen: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactAdvertPathSummary {
  public_key: string;
  paths: ContactAdvertPath[];
}

export interface ContactNameHistory {
  name: string;
  first_seen: number;
  last_seen: number;
}

export interface ContactActiveRoom {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

export interface NearestRepeater {
  public_key: string;
  name: string | null;
  path_len: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactAnalyticsHourlyBucket {
  bucket_start: number;
  last_24h_count: number;
  last_week_average: number;
  all_time_average: number;
}

export interface ContactAnalyticsWeeklyBucket {
  bucket_start: number;
  message_count: number;
}

export interface ContactAnalytics {
  lookup_type: 'contact' | 'name';
  name: string;
  contact: Contact | null;
  name_first_seen_at: number | null;
  name_history: ContactNameHistory[];
  dm_message_count: number;
  channel_message_count: number;
  includes_direct_messages: boolean;
  most_active_rooms: ContactActiveRoom[];
  advert_paths: ContactAdvertPath[];
  advert_frequency: number | null;
  nearest_repeaters: NearestRepeater[];
  hourly_activity: ContactAnalyticsHourlyBucket[];
  weekly_activity: ContactAnalyticsWeeklyBucket[];
}

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  flood_scope_override?: string | null;
  path_hash_mode_override?: number | null;
  last_read_at: number | null;
  favorite: boolean;
  muted: boolean;
}

export interface ChannelMessageCounts {
  last_1h: number;
  last_24h: number;
  last_48h: number;
  last_7d: number;
  all_time: number;
}

export interface ChannelTopSender {
  sender_name: string;
  sender_key: string | null;
  message_count: number;
}

export interface BulkCreateHashtagChannelsResult {
  created_channels: Channel[];
  existing_count: number;
  invalid_names: string[];
  decrypt_started: boolean;
  decrypt_total_packets: number;
  message: string;
}

export interface PathHashWidthStats {
  total_packets: number;
  single_byte: number;
  double_byte: number;
  triple_byte: number;
  single_byte_pct: number;
  double_byte_pct: number;
  triple_byte_pct: number;
}

export interface ChannelDetail {
  channel: Channel;
  message_counts: ChannelMessageCounts;
  first_message_at: number | null;
  unique_sender_count: number;
  top_senders_24h: ChannelTopSender[];
  path_hash_width_24h: PathHashWidthStats;
}

/** A single path that a message took to reach us */
export interface MessagePath {
  /** Hex-encoded routing path */
  path: string;
  /** Unix timestamp when this path was received */
  received_at: number;
  /** Hop count (number of intermediate nodes). Null for legacy data (infer as len(path)/2). */
  path_len?: number | null;
  /** Last-hop RSSI in dBm (null if not available, e.g. older data) */
  rssi?: number | null;
  /** Last-hop SNR in dB (null if not available, e.g. older data) */
  snr?: number | null;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  /** For PRIV: sender's PublicKey (or prefix). For CHAN: ChannelKey */
  conversation_key: string;
  text: string;
  sender_timestamp: number | null;
  received_at: number;
  /** List of routing paths this message arrived via. Null for outgoing messages. */
  paths: MessagePath[] | null;
  txt_type: number;
  signature: string | null;
  sender_key: string | null;
  outgoing: boolean;
  /** ACK count: 0 = not acked, 1+ = number of acks/flood echoes received */
  acked: number;
  sender_name: string | null;
  channel_name?: string | null;
  packet_id?: number | null;
  /** Region scope transport code (uint16) when this arrived via a transport-routed packet. */
  transport_code?: number | null;
  /** Resolved region name for the transport code, if it matched a known region. */
  region?: string | null;
  /** Firmware packet hash (16 hex) for observer-reach lookup. */
  packet_hash?: string | null;
  /** True for CHAN and PRIV flood; persisted so the badge survives raw-packet purge. */
  observer_reach_eligible?: boolean | null;
}

export interface MessagesAroundResponse {
  messages: Message[];
  has_older: boolean;
  has_newer: boolean;
}

export interface ResendChannelMessageResponse {
  status: string;
  message_id: number;
  message?: Message;
}

type ConversationType =
  'contact' | 'channel' | 'raw' | 'map' | 'live' | 'visualizer' | 'search' | 'trace' | 'locate';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw'/'map' for special views */
  id: string;
  name: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
  /** For locate view: key, prefix, or search token */
  locateKey?: string;
}

export type LocateSource = 'local' | 'corescope' | 'mixte';
export type LocateAnchorKind = 'local_0hop' | 'first_hop' | 'corescope_0hop';
export type LocateEmptyReason = 'insufficient_identity' | 'directory_off' | 'no_anchors';

export interface LocateCandidate {
  public_key: string;
  name?: string | null;
  type?: number | null;
  last_seen?: number | null;
  role?: string | null;
}

export interface LocateIdentity {
  public_key: string;
  name?: string | null;
  contact_type?: number | null;
  inferred: boolean;
  last_seen?: number | null;
}

export interface LocateAnchor {
  kind: LocateAnchorKind;
  source: 'local' | 'corescope';
  name: string;
  public_key?: string | null;
  hop_prefix?: string | null;
  lat: number;
  lon: number;
  radius_km: number;
  snr?: number | null;
  heard_count?: number | null;
  last_seen?: number | null;
  calibratable: boolean;
}

export interface LocateUnresolvedHop {
  prefix: string;
  reason: 'ambiguous' | 'no_gps' | 'unmatched' | 'one_byte';
  candidates: LocateCandidate[];
}

export interface LocateDeclaredGps {
  lat: number;
  lon: number;
  source: 'advert' | 'corescope';
}

export interface LocateResponse {
  query: string;
  identity: LocateIdentity | null;
  source: LocateSource | null;
  directory_enabled: boolean;
  default_radius_km: number;
  anchors: LocateAnchor[];
  unresolved_hops: LocateUnresolvedHop[];
  declared_gps: LocateDeclaredGps | null;
  heard_locally_0hop: boolean;
  radio_has_gps: boolean;
  empty_reason: LocateEmptyReason | null;
}

export interface DirectoryReachResponse {
  node: {
    public_key: string;
    name?: string | null;
    role?: string | null;
    lat?: number | null;
    lon?: number | null;
  } | null;
  observers: Array<{
    public_key: string;
    name: string;
    count: number;
    avg_snr?: number | null;
    lat?: number | null;
    lon?: number | null;
  }>;
  directory_enabled: boolean;
}

export interface DirectoryNeighborsResponse {
  neighbors: Array<{
    public_key?: string | null;
    prefix?: string | null;
    name?: string | null;
    count: number;
    score?: number | null;
    avg_snr?: number | null;
    lat?: number | null;
    lon?: number | null;
    ambiguous: boolean;
  }>;
  directory_enabled: boolean;
}

export interface DirectoryNodeSearchResponse {
  nodes: Array<{
    public_key: string;
    name?: string | null;
    role?: string | null;
    lat?: number | null;
    lon?: number | null;
    last_seen?: string | null;
  }>;
  directory_enabled: boolean;
}

/** Known MeshCore payload tokens on the community live feed. */
export type KnownCommunityPacketType =
  | 'req'
  | 'response'
  | 'text'
  | 'ack'
  | 'advert'
  | 'grp_txt'
  | 'grp_data'
  | 'anon_req'
  | 'path'
  | 'trace'
  | 'multipart'
  | 'control'
  | 'raw_custom'
  | 'other';

/** Known tokens plus any future Stats token. Unknown tokens still draw. */
export type CommunityPacketType = KnownCommunityPacketType | (string & {});

/**
 * How much a hop position can be trusted. `probable` is a geographically
 * filtered guess and must stay visually distinct from `exact`.
 */
export type CommunityHopConfidence = 'exact' | 'probable' | 'unresolved';

export interface CommunityPacketHop {
  token: string;
  confidence: CommunityHopConfidence;
  /** Present when confidence is `exact` or `probable`, absent otherwise. */
  lat?: number;
  lon?: number;
  /** Machine token for the tooltip. Required unless confidence is `exact`. */
  reason?: string;
  /** From signed adverts; only offered on `exact`. */
  pubkey?: string;
  name?: string;
}

/** Observer position. `advert` is the real node GPS, `iata` a region centroid. */
export interface CommunityPacketEar {
  lat: number;
  lon: number;
  source: 'advert' | 'iata';
}

/**
 * Server → Meshloom `community_packet` frame (v2, stats `live-events.md`).
 * Browser never talks to Stats. Extra fields must be ignored.
 */
export interface CommunityPacket {
  v: number;
  event_id: string;
  hash8: string;
  /** Canonical firmware packet hash (16 hex lowercase). Absent on older Stats frames. */
  packet_hash?: string;
  type: CommunityPacketType;
  path: string[];
  hop_count: number;
  hops: CommunityPacketHop[];
  /** Null when neither an advert position nor an IATA centroid is known. */
  ear: CommunityPacketEar | null;
  /** Advertiser hop. Pubkey matches the pin already on the map. */
  origin?: CommunityPacketHop;
  /** MeshCore route_type when Stats persisted it. Absent on older frames. */
  route_kind?: 'flood' | 'direct' | 'unknown';
  snr?: number;
  iata: string;
  t: number;
  ear_id: string;
}

export const LIVE_CLOSE_JWT_EXPIRED = 4001;
export const LIVE_CLOSE_INACTIVE = 4002;
/** Retired in contract v2. Kept so a v1 server response still maps somewhere. */
export const LIVE_CLOSE_SLOT_BUSY = 4003;
export const LIVE_CLOSE_RATE_LIMIT = 4004;
export const LIVE_CLOSE_SUPERSEDED = 4005;

export type LiveCloseCode =
  | typeof LIVE_CLOSE_JWT_EXPIRED
  | typeof LIVE_CLOSE_INACTIVE
  | typeof LIVE_CLOSE_SLOT_BUSY
  | typeof LIVE_CLOSE_RATE_LIMIT
  | typeof LIVE_CLOSE_SUPERSEDED;

export interface CommunityLiveStatus {
  session_id?: string | null;
  close_code: LiveCloseCode | null;
  opted_out: boolean;
  connected: boolean;
  /** Absent on older relays — derive reconnecting from close_code instead. */
  reconnecting?: boolean;
}

export interface DirectoryMapNodesQuery {
  limit?: number;
  offset?: number;
  /** Omitted means every role, not repeaters only. */
  role?: DirectoryNodeRole;
}

export interface RawPacket {
  id: number;
  /** Per-observation WS identity (unique per RF arrival, may be absent in older payloads) */
  observation_id?: number;
  timestamp: number;
  data: string; // hex
  payload_type: string;
  snr: number | null; // Signal-to-noise ratio in dB
  rssi: number | null; // Received signal strength in dBm
  decrypted: boolean;
  decrypted_info: {
    channel_name: string | null;
    sender: string | null;
    channel_key: string | null;
    contact_key: string | null;
    sender_timestamp: number | null;
    message: string | null;
  } | null;
  /** Region scope transport code (uint16) for TransportFlood/TransportDirect packets. */
  transport_code?: number | null;
  /** Resolved region name for the transport code, if it matched a known region. */
  region?: string | null;
  /** Firmware packet hash (16 hex). First 8 lowercase chars are #live hash8. */
  packet_hash?: string | null;
}

export interface GroupTextSample {
  channel_hash: string;
  packet_id: number;
  data: string;
  timestamp: number;
  cipher_mac: string;
}

export interface GroupTextSamplesResponse {
  hash_count: number;
  packet_count: number;
  scanned: number;
  samples: GroupTextSample[];
}

/**
 * Interface preferences, kept with the instance rather than in a browser.
 *
 * One Meshloom is reached from a phone and a desktop by the same person — there is
 * no notion of separate users, a single credential guards the whole instance — so
 * a preference stored per browser had to be set again on every device.
 */
/** A name for this instance, shown across the top of every page. */
export interface ServerLabel {
  /** Empty hides the band, which is the default. */
  text: string;
  color: string;
  /** Text size in pixels, which also sets the band's height. */
  size_px: number;
  bold: boolean;
  italic: boolean;
  /** Families every browser has; a named font nobody installed renders as another. */
  font: 'system' | 'serif' | 'mono';
}

export interface UiPreferences {
  /** The desktop rail's contents, in order. Empty means the defaults. */
  nav_rail: string[];
  /** Theme id, or empty to follow the operating system. */
  theme: string;
  /** Names the server rather than the device, so it shows from any browser. */
  server_label?: ServerLabel;
}

export type TelemetryAlertOp = 'lt' | 'gt';
export type NotificationDestinationChannel = 'email' | 'webhook';
export type NotificationEmailMode = 'none' | 'starttls' | 'ssl';

export interface TelemetryAlertRuleSpec {
  enabled: boolean;
  op?: TelemetryAlertOp;
  threshold?: number;
  hysteresis?: number;
}

export interface TelemetryAlertRuleOverrideSpec {
  enabled?: boolean;
  threshold?: number;
}

export interface TelemetryAlertNodeOverride {
  alerting?: boolean;
  rules?: Record<string, TelemetryAlertRuleOverrideSpec>;
}

/** Telemetry-alert v2 document persisted on GET/PATCH /api/settings. */
export interface TelemetryAlertRules {
  channels: { push: boolean; email: boolean; webhook: boolean };
  rules: Record<string, TelemetryAlertRuleSpec>;
  overrides: Record<string, TelemetryAlertNodeOverride>;
}

export interface NotificationEmailDest {
  host: string;
  port: number;
  mode: NotificationEmailMode;
  user: string;
  password: string;
  from: string;
  to: string;
}

export interface NotificationWebhookDest {
  url: string;
  hmac_secret: string;
}

export interface NotificationDestinations {
  email: NotificationEmailDest;
  webhook: NotificationWebhookDest;
}

/** PATCH omit/null keeps a secret; empty string clears it. */
export interface NotificationDestinationsUpdate {
  email?: Partial<Omit<NotificationEmailDest, 'password'>> & { password?: string | null };
  webhook?: Partial<Omit<NotificationWebhookDest, 'hmac_secret'>> & {
    hmac_secret?: string | null;
  };
}

export interface TelemetryAlertCatalogMetric {
  id: string;
  label_key?: string;
  type?: string;
  unit?: string;
  source?: string;
}

export interface TelemetryAlertLatch {
  public_key: string;
  rule_id: string;
  last_fired_at: number;
  last_value?: number | null;
}

export interface TelemetryAlertTrackedNode {
  public_key: string;
  name: string;
  alerting: boolean;
}

export interface TelemetryAlertCatalog {
  metrics: TelemetryAlertCatalogMetric[];
  latches: TelemetryAlertLatch[];
  tracked: TelemetryAlertTrackedNode[];
}

export const BUILTIN_TELEMETRY_ALERT_RULE_IDS = [
  'battery',
  'noise',
  'rssi',
  'snr',
  'tx_queue',
  'silence',
  'gps_lost',
] as const;

export const LPP_TELEMETRY_ALERT_RULE_IDS = [
  'lpp:temperature',
  'lpp:humidity',
  'lpp:barometer',
  'lpp:voltage',
  'lpp:current',
  'lpp:luminosity',
  'lpp:altitude',
  'lpp:power',
  'lpp:distance',
  'lpp:energy',
  'lpp:direction',
  'lpp:concentration',
] as const;

export const TELEMETRY_ALERT_RULE_IDS = [
  ...BUILTIN_TELEMETRY_ALERT_RULE_IDS,
  ...LPP_TELEMETRY_ALERT_RULE_IDS,
] as const;

export const SECRET_REDACTED = '********';

export const DEFAULT_NOTIFICATION_DESTINATIONS: NotificationDestinations = {
  email: {
    host: '',
    port: 587,
    mode: 'starttls',
    user: '',
    password: '',
    from: '',
    to: '',
  },
  webhook: { url: '', hmac_secret: '' },
};

export const DEFAULT_TELEMETRY_ALERT_RULES: TelemetryAlertRules = {
  channels: { push: true, email: false, webhook: false },
  rules: {
    battery: { enabled: true, op: 'lt', threshold: 3.5, hysteresis: 0.2 },
    noise: { enabled: true, op: 'gt', threshold: -90, hysteresis: 3 },
    rssi: { enabled: false, op: 'lt', threshold: -120, hysteresis: 5 },
    snr: { enabled: false, op: 'lt', threshold: 0, hysteresis: 2 },
    tx_queue: { enabled: false, op: 'gt', threshold: 10, hysteresis: 2 },
    silence: { enabled: true, threshold: 2 },
    gps_lost: { enabled: true },
    'lpp:temperature': { enabled: false, op: 'gt', threshold: 50, hysteresis: 1 },
    'lpp:humidity': { enabled: false, op: 'gt', threshold: 90, hysteresis: 3 },
    'lpp:barometer': { enabled: false, op: 'lt', threshold: 980, hysteresis: 5 },
    'lpp:voltage': { enabled: false, op: 'lt', threshold: 3.5, hysteresis: 0.1 },
    'lpp:current': { enabled: false, op: 'gt', threshold: 1, hysteresis: 0.1 },
    'lpp:luminosity': { enabled: false, op: 'lt', threshold: 10, hysteresis: 5 },
    'lpp:altitude': { enabled: false, op: 'gt', hysteresis: 10 },
    'lpp:power': { enabled: false, op: 'gt' },
    'lpp:distance': { enabled: false, op: 'gt' },
    'lpp:energy': { enabled: false, op: 'gt' },
    'lpp:direction': { enabled: false, op: 'gt' },
    'lpp:concentration': { enabled: false, op: 'gt' },
  },
  overrides: {},
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asFiniteNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function asUnixSeconds(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e12 ? Math.floor(value / 1000) : value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric > 1e12 ? Math.floor(numeric / 1000) : numeric;
    const ms = Date.parse(value);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  return 0;
}

function mergeRuleSpec(
  fallback: TelemetryAlertRuleSpec | undefined,
  incoming: unknown
): TelemetryAlertRuleSpec {
  const raw = asRecord(incoming) ?? {};
  const enabled = typeof raw.enabled === 'boolean' ? raw.enabled : (fallback?.enabled ?? false);
  const op = raw.op === 'lt' || raw.op === 'gt' ? raw.op : fallback?.op;
  const threshold = asFiniteNumber(raw.threshold) ?? fallback?.threshold;
  const hysteresis = asFiniteNumber(raw.hysteresis) ?? fallback?.hysteresis;
  return {
    enabled,
    ...(op ? { op } : {}),
    ...(threshold !== undefined ? { threshold } : {}),
    ...(hysteresis !== undefined ? { hysteresis } : {}),
  };
}

function isNewTelemetryAlertRulesShape(raw: Record<string, unknown>): boolean {
  return asRecord(raw.channels) != null || asRecord(raw.rules) != null;
}

function migrateLegacyTelemetryAlertRules(raw: Record<string, unknown>): TelemetryAlertRules {
  const next = structuredClone(DEFAULT_TELEMETRY_ALERT_RULES);
  const battery = asFiniteNumber(raw.battery_volts_min);
  const noise = asFiniteNumber(raw.noise_floor_max_dbm);
  const misses = asFiniteNumber(raw.misses_before_alert);
  if (battery !== undefined) next.rules.battery = { ...next.rules.battery, threshold: battery };
  if (noise !== undefined) next.rules.noise = { ...next.rules.noise, threshold: noise };
  if (misses !== undefined) next.rules.silence = { ...next.rules.silence, threshold: misses };

  const rawOverrides = asRecord(raw.overrides);
  if (rawOverrides) {
    for (const [key, value] of Object.entries(rawOverrides)) {
      const override = asRecord(value);
      if (!key || !override) continue;
      const rules: Record<string, TelemetryAlertRuleOverrideSpec> = {};
      const batteryOverride = asFiniteNumber(override.battery_volts_min);
      const noiseOverride = asFiniteNumber(override.noise_floor_max_dbm);
      const missesOverride = asFiniteNumber(override.misses_before_alert);
      if (batteryOverride !== undefined) rules.battery = { threshold: batteryOverride };
      if (noiseOverride !== undefined) rules.noise = { threshold: noiseOverride };
      if (missesOverride !== undefined) rules.silence = { threshold: missesOverride };
      next.overrides[key.toLowerCase()] = {
        ...(typeof override.alerting === 'boolean' ? { alerting: override.alerting } : {}),
        ...(Object.keys(rules).length > 0 ? { rules } : {}),
      };
    }
  }
  return next;
}

export function resolveTelemetryAlertRules(
  rules: TelemetryAlertRules | null | undefined | unknown
): TelemetryAlertRules {
  const raw = asRecord(rules);
  if (!raw) return structuredClone(DEFAULT_TELEMETRY_ALERT_RULES);
  if (!isNewTelemetryAlertRulesShape(raw)) return migrateLegacyTelemetryAlertRules(raw);

  const next = structuredClone(DEFAULT_TELEMETRY_ALERT_RULES);
  const channels = asRecord(raw.channels);
  if (channels) {
    next.channels = {
      push: typeof channels.push === 'boolean' ? channels.push : next.channels.push,
      email: typeof channels.email === 'boolean' ? channels.email : next.channels.email,
      webhook: typeof channels.webhook === 'boolean' ? channels.webhook : next.channels.webhook,
    };
  }

  const incomingRules = asRecord(raw.rules);
  if (incomingRules) {
    for (const [id, spec] of Object.entries(incomingRules)) {
      if (!id) continue;
      next.rules[id] = mergeRuleSpec(next.rules[id], spec);
    }
  }

  const incomingOverrides = asRecord(raw.overrides);
  next.overrides = {};
  if (incomingOverrides) {
    for (const [key, value] of Object.entries(incomingOverrides)) {
      const override = asRecord(value);
      if (!key || !override) continue;
      const overrideRules: Record<string, TelemetryAlertRuleOverrideSpec> = {};
      const rulesRaw = asRecord(override.rules);
      if (rulesRaw) {
        for (const [ruleId, ruleValue] of Object.entries(rulesRaw)) {
          const rule = asRecord(ruleValue);
          if (!ruleId || !rule) continue;
          const enabled = typeof rule.enabled === 'boolean' ? rule.enabled : undefined;
          const threshold = asFiniteNumber(rule.threshold);
          if (enabled === undefined && threshold === undefined) continue;
          overrideRules[ruleId] = {
            ...(enabled !== undefined ? { enabled } : {}),
            ...(threshold !== undefined ? { threshold } : {}),
          };
        }
      }
      next.overrides[key.toLowerCase()] = {
        ...(typeof override.alerting === 'boolean' ? { alerting: override.alerting } : {}),
        ...(Object.keys(overrideRules).length > 0 ? { rules: overrideRules } : {}),
      };
    }
  }
  return next;
}

export function resolveNotificationDestinations(
  dest: NotificationDestinations | null | undefined | unknown
): NotificationDestinations {
  const raw = asRecord(dest);
  const email = asRecord(raw?.email);
  const webhook = asRecord(raw?.webhook);
  const mode = email?.mode;
  return {
    email: {
      host: typeof email?.host === 'string' ? email.host : '',
      port: asFiniteNumber(email?.port) ?? DEFAULT_NOTIFICATION_DESTINATIONS.email.port,
      mode: mode === 'none' || mode === 'starttls' || mode === 'ssl' ? mode : 'starttls',
      user: typeof email?.user === 'string' ? email.user : '',
      password: typeof email?.password === 'string' ? email.password : '',
      from: typeof email?.from === 'string' ? email.from : '',
      to: typeof email?.to === 'string' ? email.to : '',
    },
    webhook: {
      url: typeof webhook?.url === 'string' ? webhook.url : '',
      hmac_secret: typeof webhook?.hmac_secret === 'string' ? webhook.hmac_secret : '',
    },
  };
}

export function isEmailDestinationReady(email: NotificationEmailDest | undefined): boolean {
  return Boolean(email?.host?.trim() && email.to.trim());
}

export function isWebhookDestinationReady(webhook: NotificationWebhookDest | undefined): boolean {
  return Boolean(webhook?.url?.trim());
}

function secretForPatch(draft: string, stored: string | undefined): string | undefined {
  if (draft === SECRET_REDACTED) return undefined;
  const current = stored ?? '';
  if (draft === current) return undefined;
  return draft;
}

export function buildNotificationDestinationsPatch(
  draft: NotificationDestinations,
  stored: NotificationDestinations | null | undefined
): NotificationDestinationsUpdate {
  const current = stored ?? DEFAULT_NOTIFICATION_DESTINATIONS;
  const emailPassword = secretForPatch(draft.email.password, current.email.password);
  const hmacSecret = secretForPatch(draft.webhook.hmac_secret, current.webhook.hmac_secret);
  return {
    email: {
      host: draft.email.host,
      port: draft.email.port,
      mode: draft.email.mode,
      user: draft.email.user,
      from: draft.email.from,
      to: draft.email.to,
      ...(emailPassword !== undefined ? { password: emailPassword } : {}),
    },
    webhook: {
      url: draft.webhook.url,
      ...(hmacSecret !== undefined ? { hmac_secret: hmacSecret } : {}),
    },
  };
}

export function normalizeTelemetryAlertCatalog(raw: unknown): TelemetryAlertCatalog {
  const obj = asRecord(raw) ?? {};
  const metricsSrc = Array.isArray(obj.metrics)
    ? obj.metrics
    : Array.isArray(obj.rules)
      ? obj.rules
      : [];
  const latchesSrc = Array.isArray(obj.latches)
    ? obj.latches
    : Array.isArray(obj.latched)
      ? obj.latched
      : Array.isArray(obj.active_latches)
        ? obj.active_latches
        : [];
  const trackedSrc = Array.isArray(obj.tracked)
    ? obj.tracked
    : Array.isArray(obj.nodes)
      ? obj.nodes
      : Array.isArray(obj.tracked_nodes)
        ? obj.tracked_nodes
        : [];

  const metrics: TelemetryAlertCatalogMetric[] = [];
  for (const item of metricsSrc) {
    if (typeof item === 'string' && item) {
      metrics.push({ id: item });
      continue;
    }
    const rec = asRecord(item);
    if (!rec) continue;
    const id =
      (typeof rec.id === 'string' && rec.id) ||
      (typeof rec.metric_id === 'string' && rec.metric_id) ||
      (typeof rec.type === 'string' && rec.type) ||
      '';
    if (!id) continue;
    metrics.push({
      id,
      ...(typeof rec.label_key === 'string' ? { label_key: rec.label_key } : {}),
      ...(typeof rec.type === 'string' ? { type: rec.type } : {}),
      ...(typeof rec.unit === 'string' ? { unit: rec.unit } : {}),
      ...(typeof rec.source === 'string' ? { source: rec.source } : {}),
    });
  }

  const latches: TelemetryAlertLatch[] = [];
  for (const item of latchesSrc) {
    const rec = asRecord(item);
    if (!rec) continue;
    const publicKey =
      (typeof rec.public_key === 'string' && rec.public_key) ||
      (typeof rec.pubkey === 'string' && rec.pubkey) ||
      '';
    const ruleId =
      (typeof rec.rule_id === 'string' && rec.rule_id) ||
      (typeof rec.rule === 'string' && rec.rule) ||
      (typeof rec.metric_id === 'string' && rec.metric_id) ||
      '';
    if (!publicKey || !ruleId) continue;
    latches.push({
      public_key: publicKey,
      rule_id: ruleId,
      last_fired_at: asUnixSeconds(rec.last_fired_at ?? rec.fired_at),
      last_value: asFiniteNumber(rec.last_value ?? rec.value) ?? null,
    });
  }

  const tracked: TelemetryAlertTrackedNode[] = [];
  for (const item of trackedSrc) {
    const rec = asRecord(item);
    if (!rec) continue;
    const publicKey =
      (typeof rec.public_key === 'string' && rec.public_key) ||
      (typeof rec.pubkey === 'string' && rec.pubkey) ||
      '';
    if (!publicKey) continue;
    tracked.push({
      public_key: publicKey,
      name: typeof rec.name === 'string' ? rec.name : publicKey.slice(0, 12),
      alerting: typeof rec.alerting === 'boolean' ? rec.alerting : rec.enabled !== false,
    });
  }

  return { metrics, latches, tracked };
}

export interface AppSettings {
  ui_preferences: UiPreferences;
  max_radio_contacts: number;
  auto_decrypt_dm_on_advert: boolean;
  last_message_times: Record<string, number>;
  advert_interval: number;
  last_advert_time: number;
  flood_scope: string;
  known_regions: string[];
  blocked_keys: string[];
  blocked_names: string[];
  discovery_blocked_types: number[];
  tracked_telemetry_repeaters: string[];
  tracked_telemetry_contacts: string[];
  auto_resend_channel: boolean;
  telemetry_interval_hours: number;
  telemetry_routed_hourly: boolean;
  stale_contact_days?: number;
  directory_available?: boolean;
  /** Absent until the backend ships the field; UI falls back to defaults. */
  telemetry_alert_rules?: TelemetryAlertRules;
  notification_destinations?: NotificationDestinations;
}

export interface AppSettingsUpdate {
  ui_preferences?: UiPreferences;
  max_radio_contacts?: number;
  auto_decrypt_dm_on_advert?: boolean;
  advert_interval?: number;
  auto_resend_channel?: boolean;
  flood_scope?: string;
  known_regions?: string[];
  blocked_keys?: string[];
  blocked_names?: string[];
  discovery_blocked_types?: number[];
  telemetry_interval_hours?: number;
  telemetry_routed_hourly?: boolean;
  stale_contact_days?: number;
  telemetry_alert_rules?: TelemetryAlertRules;
  notification_destinations?: NotificationDestinationsUpdate;
}

export interface DirectoryHopHit {
  name: string;
  source: 'corescope';
  hash_width: number;
  public_key?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export interface DirectoryResolveHopsResponse {
  resolved: Record<string, DirectoryHopHit>;
}

/** Roles the community directory reports. Empty/unknown collapses to `unknown`.
 *  `companion` is the live-map word; `client` remains an import alias. */
export type DirectoryNodeRole =
  'repeater' | 'room' | 'client' | 'companion' | 'sensor' | 'observer' | 'unknown';

export type DirectoryNodeSource = 'corescope' | 'community-db' | 'local';

export interface DirectoryMapNode {
  public_key: string;
  name: string;
  role: DirectoryNodeRole;
  lat: number;
  lon: number;
  source: DirectoryNodeSource;
  last_seen?: number | null;
}

export interface DirectoryMapNodesResponse {
  nodes: DirectoryMapNode[];
  /** Total available upstream, so a caller can tell a page from the whole set. */
  total?: number;
}

export interface ObserverReachEntry {
  name: string;
  public_key?: string | null;
  lat?: number | null;
  lon?: number | null;
  hops?: number | null;
  snr?: number | null;
  path?: string[];
  isMLC?: boolean;
}

export interface ObserverReachMapHop {
  prefix: string;
  hopIndex: number;
  lat: number;
  lon: number;
  name: string | null;
}

export interface PacketObserverReachResponse {
  directory_enabled: boolean;
  packet_hash?: string | null;
  observer_count: number;
  observers: ObserverReachEntry[];
  max_hops?: number | null;
  max_distance_km?: number | null;
  origin_available: boolean;
  origin_lat?: number | null;
  origin_lon?: number | null;
  sealed?: boolean;
}

export interface PacketObserverReachCountsResponse {
  directory_enabled: boolean;
  counts: Record<string, number>;
  sealed?: Record<string, boolean>;
}

export type ObserverReachCountState =
  { status: 'loading' } | { status: 'ok'; count: number } | { status: 'error' };

export interface ContactGroup {
  id: number;
  name: string;
  sort_order: number;
  created_at: number;
  public_keys: string[];
}

export interface BackupContact {
  public_key: string;
  name: string | null;
  type: number;
  flags: number;
  favorite: boolean;
  first_seen?: number | null;
  last_seen?: number | null;
}

export interface BackupChannel {
  key: string;
  name: string;
  is_hashtag: boolean;
  favorite: boolean;
  muted: boolean;
  flood_scope_override?: string | null;
  path_hash_mode_override?: number | null;
}

export interface BackupExport {
  format: string;
  exported_at: number;
  contacts: BackupContact[];
  channels: BackupChannel[];
  settings: AppSettings | null;
  groups: ContactGroup[];
}

export interface BackupRestoreResult {
  contacts_upserted: number;
  channels_upserted: number;
  settings_updated: boolean;
  groups_upserted: number;
  private_key_warning: string;
}

export interface TelemetrySchedule {
  preferred_hours: number;
  effective_hours: number;
  options: number[];
  tracked_count: number;
  max_tracked: number;
  next_run_at: number | null;
  routed_hourly: boolean;
  next_routed_run_at: number | null;
}

export interface TrackedTelemetryResponse {
  tracked_telemetry_repeaters: string[];
  names: Record<string, string>;
  schedule: TelemetrySchedule;
}

/** Contact type constants */
export const CONTACT_TYPE_REPEATER = 2;
export const CONTACT_TYPE_ROOM = 3;
export const CONTACT_TYPE_SENSOR = 4;

export interface NeighborInfo {
  pubkey_prefix: string;
  name: string | null;
  snr: number;
  last_heard_seconds: number;
}

export interface AclEntry {
  pubkey_prefix: string;
  name: string | null;
  permission: number;
  permission_name: string;
}

export interface CommandResponse {
  command: string;
  response: string;
  sender_timestamp: number | null;
}

// --- Granular repeater endpoint types ---

export interface RepeaterLoginResponse {
  status: string;
  authenticated: boolean;
  message: string | null;
}

export interface RepeaterStatusResponse {
  battery_volts: number;
  tx_queue_len: number;
  noise_floor_dbm: number;
  last_rssi_dbm: number;
  last_snr_db: number;
  packets_received: number;
  packets_sent: number;
  airtime_seconds: number;
  rx_airtime_seconds: number;
  uptime_seconds: number;
  sent_flood: number;
  sent_direct: number;
  recv_flood: number;
  recv_direct: number;
  flood_dups: number;
  direct_dups: number;
  full_events: number;
  recv_errors: number | null;
  telemetry_history: TelemetryHistoryEntry[];
}

export interface RepeaterNeighborsResponse {
  neighbors: NeighborInfo[];
  // Total neighbor count reported by the repeater firmware, independent of how many
  // entries were actually returned. Exceeds neighbors.length when a multi-chunk fetch
  // is incomplete. Null on older firmware / failed fetches.
  reported_count?: number | null;
}

export interface RepeaterAclResponse {
  acl: AclEntry[];
}

export interface RepeaterNodeInfoResponse {
  name: string | null;
  lat: string | null;
  lon: string | null;
  clock_utc: string | null;
}

export interface RepeaterRadioSettingsResponse {
  firmware_version: string | null;
  radio: string | null;
  tx_power: string | null;
  airtime_factor: string | null;
  // Configured duty-cycle limit (e.g. "25.0%"), firmware-derived from airtime_factor.
  // Only present on firmware >= 1.15; null on older nodes.
  duty_cycle_limit: string | null;
  repeat_enabled: string | null;
  flood_max: string | null;
}

export interface RepeaterAdvertIntervalsResponse {
  advert_interval: string | null;
  flood_advert_interval: string | null;
}

export interface RepeaterOwnerInfoResponse {
  owner_info: string | null;
  firmware_version: string | null;
  name: string | null;
  guest_password: string | null;
}

export interface RepeaterRegionEntry {
  name: string;
  depth: number;
  flood_allowed: boolean;
  is_home: boolean;
}

export interface RepeaterRegionsResponse {
  regions: RepeaterRegionEntry[];
  raw: string | null;
  truncated: boolean;
  /** 'cli' = full admin hierarchy; 'anon' = guest flood-allowed names only. */
  source: 'cli' | 'anon' | null;
}

export interface LppSensor {
  channel: number;
  type_name: string;
  value: number | Record<string, number>;
}

export interface RepeaterLppTelemetryResponse {
  sensors: LppSensor[];
}

export interface ContactTelemetryResponse {
  sensors: LppSensor[];
  fetched_at: number;
  telemetry_history: TelemetryHistoryEntry[];
}

export interface TrackedTelemetryContactsResponse {
  tracked_telemetry_contacts: string[];
  names: Record<string, string>;
  schedule: TelemetrySchedule;
}

export type PaneName =
  | 'status'
  | 'nodeInfo'
  | 'neighbors'
  | 'acl'
  | 'radioSettings'
  | 'advertIntervals'
  | 'ownerInfo'
  | 'lppTelemetry'
  | 'regions';

export interface PaneState {
  loading: boolean;
  attempt: number;
  error: string | null;
  /** Waiting its turn in a serial Load All run. */
  queued?: boolean;
  fetched_at?: number | null;
}

export interface TelemetryLppSensor {
  channel: number;
  type_name: string;
  value: number;
}

export interface TelemetryHistoryEntry {
  timestamp: number;
  data: Record<string, number> & { lpp_sensors?: TelemetryLppSensor[] };
}

export interface PushSubscriptionInfo {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
  label: string;
  language: 'fr' | 'en';
  created_at: number;
  last_success_at: number | null;
  failure_count: number;
}

export interface NotificationMediaFlags {
  push: boolean;
  email: boolean;
  webhook: boolean;
}

export type NotificationMediaChannel = keyof NotificationMediaFlags;

export const DEFAULT_NOTIFICATION_MEDIA: NotificationMediaFlags = {
  push: true,
  email: false,
  webhook: false,
};

export function resolveNotificationMedia(raw: unknown): NotificationMediaFlags {
  if (typeof raw === 'boolean') {
    return { push: raw, email: false, webhook: false };
  }
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    return {
      push: Boolean(obj.push),
      email: Boolean(obj.email),
      webhook: Boolean(obj.webhook),
    };
  }
  return { ...DEFAULT_NOTIFICATION_MEDIA };
}

export function notificationMediaFlag(
  raw: unknown,
  channel: NotificationMediaChannel = 'push'
): boolean {
  return resolveNotificationMedia(raw)[channel];
}

export interface PushDefaults {
  new_contact: NotificationMediaFlags;
  new_dm: NotificationMediaFlags;
  advert_repeater: NotificationMediaFlags;
  advert_companion: NotificationMediaFlags;
  advert_sensor: NotificationMediaFlags;
  channel_found: NotificationMediaFlags;
  telemetry_alert: NotificationMediaFlags;
  oss_update: NotificationMediaFlags;
}

export type PushDefaultsPatch = {
  [K in keyof PushDefaults]?: Partial<NotificationMediaFlags> | boolean;
};

export interface PushPreferences {
  defaults: PushDefaults;
  overrides: Record<string, boolean>;
  vapid_subject: string;
}

export interface TraceResponse {
  remote_snr: number | null;
  local_snr: number | null;
  path_len: number;
}

export interface RadioTraceNode {
  role: 'repeater' | 'custom' | 'local';
  public_key: string | null;
  name: string | null;
  observed_hash: string | null;
  snr: number | null;
}

export interface RadioTraceHopRequest {
  public_key?: string | null;
  hop_hex?: string | null;
}

export interface RadioTraceResponse {
  path_len: number;
  timeout_seconds: number;
  nodes: RadioTraceNode[];
}

export interface PathDiscoveryRoute {
  path: string;
  path_len: number;
  path_hash_mode: number;
}

export interface PathDiscoveryResponse {
  contact: Contact;
  forward_path: PathDiscoveryRoute;
  return_path: PathDiscoveryRoute;
}

export interface UnreadCounts {
  counts: Record<string, number>;
  mentions: Record<string, boolean>;
  last_message_times: Record<string, number>;
  /** stateKey -> last message text, truncated to ~120 characters. */
  last_message_previews: Record<string, string>;
  last_read_ats: Record<string, number | null>;
  /** stateKey -> id of the oldest unread message. Locates the unread divider. */
  first_unread_ids: Record<string, number | null>;
}

interface BusyChannel {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

interface ContactActivityCounts {
  last_hour: number;
  last_24_hours: number;
  last_week: number;
}

export interface NoiseFloorSample {
  timestamp: number;
  noise_floor_dbm: number;
}

export interface NoiseFloorHistoryStats {
  sample_interval_seconds: number;
  coverage_seconds: number;
  latest_noise_floor_dbm: number | null;
  latest_timestamp: number | null;
  samples: NoiseFloorSample[];
}

interface PacketsPerHourBucket {
  timestamp: number;
  count: number;
}

/**
 * Regional flood-scope adoption over the last 24h. Two views with different
 * denominators that will not agree — traffic spans all channels including
 * undecryptable ones (so it carries a false-positive floor from corrupt RF
 * captures), while senders requires decryption and is therefore noise-free but
 * limited to channels we hold keys for.
 */
export interface RegionScopeStats {
  total_messages: number;
  scoped_messages: number;
  scoped_pct: number;
  /** Estimated false positives in scoped_messages. At or below this = not adoption. */
  false_positive_floor: number;
  total_senders: number;
  scoped_senders: number;
  scoped_senders_pct: number;
}

export interface StatisticsResponse {
  busiest_channels_24h: BusyChannel[];
  contact_count: number;
  repeater_count: number;
  channel_count: number;
  total_packets: number;
  decrypted_packets: number;
  undecrypted_packets: number;
  total_dms: number;
  total_channel_messages: number;
  total_outgoing: number;
  contacts_heard: ContactActivityCounts;
  repeaters_heard: ContactActivityCounts;
  known_channels_active: ContactActivityCounts;
  path_hash_width_24h: {
    total_packets: number;
    single_byte: number;
    double_byte: number;
    triple_byte: number;
    single_byte_pct: number;
    double_byte_pct: number;
    triple_byte_pct: number;
  };
  region_scope_24h: RegionScopeStats;
  packets_per_hour_72h: PacketsPerHourBucket[];
  noise_floor_24h: NoiseFloorHistoryStats;
}

/** Local Meshloom Stats join state. Browser talks only to the Meshloom backend. */
export interface CommunityStatus {
  enabled: boolean;
  locked: boolean;
  iata: string;
  broker_host: string;
  api_base: string;
  publisher_configured: boolean;
  publisher_connected: boolean;
  env_seeded: boolean;
}

export interface CommunityUpdate {
  enabled?: boolean;
  iata?: string;
  broker_host?: string;
  api_base?: string;
}

export interface CommunityIataBindRequest {
  iata: string;
  lat?: number;
  lon?: number;
}

export interface CommunityIataBindResult {
  iata: string;
  concordance: string;
  honored_for_buckets: boolean;
  distance_km: number | null;
}

export interface CommunityMeStats {
  unique_hashes_24h: number;
  unique_hashes_7d: number;
  iata: string;
  concordance: string;
  rank_in_iata: number | null;
}

export interface CommunityPublicStats {
  observers_online: number;
  iata_active: number;
  unique_hashes_24h: number;
}

export interface CommunityHashtag {
  name: string;
  hash_byte: string;
}

export interface CommunityHashtagsResponse {
  hashtags: CommunityHashtag[];
}

export interface CommunityAirportHit {
  iata: string;
  name: string;
  city: string;
  country: string;
  label: string;
  lat?: number | null;
  lon?: number | null;
}

export type OssUpdateInstallKind = 'package' | 'compose' | 'addon' | 'container' | 'source';

export type OssUpdateJobState = 'idle' | 'applying' | 'succeeded' | 'failed';

export type OssUpdateJobPhase = 'preparing' | 'downloading' | 'installing' | 'restarting' | 'done';

export interface OssUpdateJob {
  state: OssUpdateJobState;
  phase: OssUpdateJobPhase | null;
  percent: number | null;
  error: string | null;
  started_at: number | null;
}

/** Cached GET /api/updates — Stats catalogue, not GitHub from the browser. */
export interface OssUpdateStatus {
  current: string;
  latest: string | null;
  update_available: boolean;
  html_url: string | null;
  install_kind: OssUpdateInstallKind;
  apply_supported: boolean;
  auto_update: boolean;
  auto_update_window_start?: string | null;
  auto_update_window_end?: string | null;
  auto_update_weekdays?: number[];
  checked_at?: number | null;
  tz_name?: string | null;
  next_auto_apply_at?: number | null;
  job: OssUpdateJob;
}

export interface OssUpdateSettingsPatch {
  auto_update?: boolean;
  auto_update_window_start?: string;
  auto_update_window_end?: string;
  auto_update_weekdays?: number[];
}
