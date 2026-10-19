interface RadioSettings {
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export type RadioTransportKind = 'serial' | 'tcp' | 'ble';

/** Toast id for "radio rebooting to reset its clock": one toast, whoever announces it. */
export const RADIO_REBOOTING_TOAST_ID = 'radio-rebooting';

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

export type RadioBlePairingState =
  | 'unsupported'
  | 'unavailable'
  | 'not_configured'
  | 'unknown_device'
  | 'not_paired'
  | 'pairing'
  | 'paired'
  | 'failed';

/** GET/POST/DELETE /api/radio/transport/ble-pairing. Never carries the PIN. */
export interface RadioBlePairingStatus {
  supported: boolean;
  state: RadioBlePairingState;
  address: string;
  /** BlueZ adapter name, e.g. "hci0". */
  adapter: string;
  paired: boolean | null;
  trusted: boolean | null;
  connected: boolean | null;
  /** ble_pair_* code, translated under errors.<code>. */
  error_code: string | null;
  /** Interpolation values for errors.<code> (e.g. error). */
  error_params: Record<string, string>;
  error_at: number | null;
  auto_pair_blocked: boolean;
  retry_in: number | null;
  hint: 'stale_bond_suspected' | null;
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
  /** Kind of the transport `POST /radio/transport/revert` would restore, if any. */
  previous_transport_kind?: RadioTransportKind | null;
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

/** Why Meshloom rebooted the radio: the one-time clock fix, or applying Wi-Fi settings. */
export type RadioRebootReason = 'clock_sync' | 'wifi_config';

/** `POST /radio/reconnect`. `code: 'radio_rebooting'` (status `pending`): the radio is
 * restarting after a reboot Meshloom sent (`params.reason`), and the backend reconnects
 * on its own. */
export interface RadioReconnectResponse {
  status: string;
  message: string;
  connected: boolean;
  code?: string;
  params?: { reason?: RadioRebootReason };
}

export interface RadioIdentityActionResponse {
  status: string;
  radio_state:
    | 'connected'
    | 'initializing'
    | 'rebooting'
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
  /** PATCH answer only: what the update applied differently, e.g.
   * `client_repeat_not_allowed` (radio parameters written with repeat off). */
  warnings?: string[];
}

export interface RadioCustomVars {
  /** False when the firmware does not know the custom-vars command (old firmware). */
  supported: boolean;
  vars: Record<string, string>;
}

/** Wi-Fi of the connected radio (companion CLI, firmware v1.18+). Never holds a password.
 *  `GET /api/radio/wifi` and the `radio_wifi` WebSocket event. */
export interface RadioWifiState {
  /** `pending`: not checked yet on this connection; `failed`: see `check_error`. */
  check: 'pending' | 'done' | 'failed';
  check_error?: 'probe_failed' | 'cli_timeout' | 'cli_error' | null;
  /** Epoch seconds of the next automatic retry, null when none is left. */
  next_retry_at?: number | null;
  supported?: boolean | null;
  unsupported_reason?: 'no_cli' | 'no_wifi' | null;
  /** Stored SSID, or the one built into the firmware (the radio cannot tell which). */
  ssid?: string | null;
  enabled?: boolean | null;
  connected?: boolean | null;
  ip?: string | null;
  tcp_port: number;
  credentials_stored?: boolean | null;
  open_network: boolean;
  prompt_needed: boolean;
  reboot_pending: boolean;
  /** Storing credentials was interrupted; store them again before rebooting. */
  store_incomplete: boolean;
  store_target_ssid?: string | null;
  radio_public_key?: string | null;
  checked_at?: number | null;
}

export interface RadioWifiCredentialsRequest {
  /** Never trimmed. */
  ssid: string;
  password?: string | null;
  open_network?: boolean;
  /** The user confirmed a reboot. */
  reboot?: boolean;
}

export interface RadioWifiEnabledRequest {
  enabled: boolean;
  reboot?: boolean;
}

export interface RadioWifiPromptDismissRequest {
  radio_public_key: string;
}

export interface RadioWifiChangeResult {
  state: RadioWifiState;
  reboot_required: boolean;
  rebooting: boolean;
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

/** Why a repeater said nothing, when it said nothing. */
export type RadioRegionOutcome = 'answered' | 'no_reply' | 'contact_add_failed' | 'error';

export interface RadioRegionDiscoveryRepeater {
  public_key: string;
  name: string | null;
  answered: boolean;
  outcome: RadioRegionOutcome;
  detail: string | null;
  attempts: number;
  regions: string[];
}

/** One candidate name and how much stored traffic it explains. */
export interface RadioRegionMatch {
  name: string;
  packets: number;
}

export interface RadioRegionVerifyResponse {
  window_hours: number;
  packets_read: number;
  /** Of those, the ones carrying a transport code. Zero means nothing to check against. */
  regional_packets: number;
  /** True when the server chose the candidates rather than the operator. */
  proposed: boolean;
  matches: RadioRegionMatch[];
}

export interface RadioRegionDiscoveryResponse {
  repeaters_queried: number;
  repeaters_answered: number;
  /** Deduplicated union of flood-allowed region names across all repeaters. */
  regions: string[];
  results: RadioRegionDiscoveryRepeater[];
}

export type RadioAdvertMode = 'flood' | 'zero_hop';

/** One plugin instance as the plugin host sees it (health and the `plugin_status` event). */
export interface PluginStatusEntry {
  instance_id: string;
  plugin_id: string | null;
  label: string | null;
  status: string | null;
  last_error: string | null;
  dropped_events: number;
  restarts: number;
  /** The plugin's own one-line status (`status.set`), null when it set none. */
  summary?: string | null;
  /** What the plugin send guards refused since start; absent or empty when nothing. */
  send_guard?: {
    total: number;
    by_scope: Partial<Record<string, number>>;
    last: { scope: string; at: number } | null;
  };
  /** Why the instance waits for the user (`config_required`, ...); null when it does not. */
  attention?: PluginAttention | null;
}

export interface PluginAttention {
  type: string;
  [key: string]: unknown;
}

/** One plugin instance of `GET /api/plugins` (`config`, `source` and `last_error` are admin-only). */
export interface PluginInstance {
  instance_id: string;
  plugin_id: string;
  label: string;
  kind: 'bundled' | 'store' | 'custom' | 'local';
  version: string;
  enabled: boolean;
  manifest: Record<string, unknown>;
  /** Secret fields read `********`. */
  config: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  secret_fields: string[];
  scope: Record<string, unknown>;
  granted_permissions: Record<string, unknown>;
  quotas: Record<string, unknown>;
  auto_update: boolean;
  source: string | null;
  installed_at: number;
  updated_at: number;
  status: string;
  last_error: string | null;
  attention: PluginAttention | null;
  sort_order: number;
  summary: string | null;
  restarts: number;
  dropped_events: number;
}

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
  recv_errors: number | null;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  radio_initializing: boolean;
  radio_state?:
    | 'connected'
    | 'initializing'
    | 'rebooting'
    | 'connecting'
    | 'disconnected'
    | 'paused'
    | 'identity_mismatch'
    | 'identity_unbound_legacy';
  /** Set while `radio_state` is `rebooting`: why Meshloom rebooted the radio. */
  reboot_reason?: RadioRebootReason | null;
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
  /** Each notification channel's status, keyed by channel id (`builtin:push`, `legacy:webhook`...). */
  notification_statuses?: Record<string, NotificationChannelStatus>;
  bots_disabled: boolean;
  bots_disabled_source?: 'env' | 'until_restart' | null;
  /** Plugin host: isolation level, why it is off (null when on), and each instance. */
  plugins_isolation?: 'none' | 'setuid' | 'bwrap' | null;
  plugins_disabled_source?: 'env' | 'platform' | 'isolation' | null;
  plugin_statuses?: Record<string, PluginStatusEntry>;
  radio_proxy?: RadioProxyStatus | null;
}

export interface NotificationChannelStatus {
  status: 'connected' | 'error' | 'paused' | 'disconnected';
  /** A code, scrubbed of the channel's secrets; null below the admin role. */
  last_error: string | null;
  /** Epoch seconds of the last successful send. */
  last_ok_at: number | null;
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
  pinned?: boolean;
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
  pinned?: boolean;
  muted: boolean;
  muted_until?: number | null;
  membership?: 'adopted' | 'pending';
}

export interface RejectedChannel {
  key: string;
  name: string;
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
  /** WS `message` events only: true when produced by historical decryption. Absent otherwise. */
  historical?: boolean;
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
  | 'contact'
  | 'channel'
  | 'raw'
  | 'map'
  | 'live'
  | 'visualizer'
  | 'search'
  | 'trace'
  | 'locate'
  | 'discovered'
  | 'test'
  | 'feed';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw'/'map' for special views */
  id: string;
  name: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
  /** For locate view: key, prefix, or search token */
  locateKey?: string;
  /** For the feed view: preset folded into it (`#control`, the Control preset) */
  feedPreset?: 'control';
  /** For the feed view: its filters as a canonical hash query, no `?` */
  feedQuery?: string;
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

export interface DirectoryReachFirstHop {
  hop_prefix: string;
  public_key: string;
  name: string;
  lat: number;
  lon: number;
  count: number;
}

export interface DirectoryReachUnresolvedHop {
  prefix: string;
  reason: 'ambiguous' | 'no_gps' | 'unmatched' | 'one_byte';
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
    /** `rounded` (about 1 km) unless Community says the position is an advert's. */
    position_precision?: PositionPrecision;
  }>;
  first_hops?: DirectoryReachFirstHop[];
  unresolved_first_hops?: DirectoryReachUnresolvedHop[];
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

/**
 * `paused`: Community paused the public feed (1013 / 503 `live_disabled`); the relay retries slowly.
 * `banned`: Community banned this radio key (403 `banned`); the relay stopped.
 */
export type LiveRelayState =
  'connected' | 'reconnecting' | 'paused' | 'opted_out' | 'idle' | 'auth_rejected' | 'banned';

/** Why Community refused the live token for good (state `auth_rejected`). */
export type LiveAuthError = 'clock_skew' | 'token_rejected';

export interface CommunityLiveStatus {
  session_id?: string | null;
  close_code: LiveCloseCode | null;
  opted_out: boolean;
  connected: boolean;
  state?: LiveRelayState;
  /** Set with state `auth_rejected`; the relay stopped retrying until a restart. */
  auth_error?: LiveAuthError | null;
  /** Community 401 `code` behind auth_error, when the server sent one. */
  auth_code?: string | null;
  /** Community server time minus this server's time, in seconds. */
  clock_skew_s?: number | null;
  /** Regions the upstream socket asked Community for; null = the global feed. */
  upstream_iata?: string[] | null;
}

export interface CommunityIataActiveEntry {
  code: string;
  unique_hashes_24h: number;
}

export interface CommunityIataActiveResponse {
  /** False when Community is off or does not serve the list yet. */
  available: boolean;
  codes: CommunityIataActiveEntry[];
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
  /** Client-only: browser Date.now() when the WS frame was recorded. Live activity
   *  (visualizer pruning) uses this so a skewed server clock cannot age packets. */
  received_at_ms?: number;
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
    group_data: null | {
      data_type: number;
      data_len: number;
      data_hex: string;
      data_text: string | null;
    };
  } | null;
  /** Region scope transport code (uint16) for TransportFlood/TransportDirect packets. */
  transport_code?: number | null;
  /** Resolved region name for the transport code, if it matched a known region. */
  region?: string | null;
  /** Firmware packet hash (16 hex). First 8 lowercase chars are #live hash8. */
  packet_hash?: string | null;
}

export interface RawPacketHistoryQuery {
  payload_type?: string;
  since?: number;
  until?: number;
  limit?: number;
  after_id?: number;
  max_scan?: number;
}

export interface RawPacketHistoryResponse {
  items: RawPacket[];
  total: number;
  truncated: boolean;
  scanned: number;
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
  /** Tool id the app opens on when the URL has no hash; empty means the conversations. */
  start_view?: string;
  /** Names the server rather than the device, so it shows from any browser. */
  server_label?: ServerLabel;
}

export type TelemetryAlertOp = 'lt' | 'gt';
export type NotificationDestinationChannel = 'email' | 'webhook' | 'apprise';
export type AppriseMessageText = 'always' | 'public_only' | 'never';
export type AppriseFormat = 'text' | 'markdown';
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

/**
 * Telemetry-alert v2 document persisted on GET/PATCH /api/settings. Where the alerts go is
 * the `telemetry.alert` row of the notification rules, not a field here; a stored `channels`
 * key from an older document is ignored.
 */
export interface TelemetryAlertRules {
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
  /** Write-only, like ``hmac_secret``: ``********`` when set (the URL often holds a token). */
  url: string;
  hmac_secret: string;
  /** Scheme and host of the stored URL (``https://host/****``), on API reads only. */
  url_redacted?: string;
  /** Egress guard opt-in: a destination on the LAN or localhost. */
  allow_private_network: boolean;
  /** Set for a webhook saved before the guard existed: shows a one-time banner. */
  private_network_grandfathered: boolean;
}

/** Apprise destination. ``urls`` (one per line) is write-only: ``********`` when set. */
export interface NotificationAppriseDest {
  urls: string;
  /** Egress guard opt-in for the hosts the URLs name (ntfy, Gotify, Home Assistant). */
  allow_private_network: boolean;
  include_message_text: AppriseMessageText;
  format: AppriseFormat;
}

export interface NotificationDestinations {
  email: NotificationEmailDest;
  webhook: NotificationWebhookDest;
  apprise: NotificationAppriseDest;
}

/** PATCH omit/null keeps a secret; empty string clears it. */
export interface NotificationDestinationsUpdate {
  email?: Partial<Omit<NotificationEmailDest, 'password'>> & { password?: string | null };
  webhook?: Partial<Omit<NotificationWebhookDest, 'hmac_secret'>> & {
    hmac_secret?: string | null;
  };
  apprise?: Partial<Omit<NotificationAppriseDest, 'urls'>> & { urls?: string | null };
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
  webhook: {
    url: '',
    hmac_secret: '',
    allow_private_network: false,
    private_network_grandfathered: false,
  },
  apprise: {
    urls: '',
    allow_private_network: false,
    include_message_text: 'public_only',
    format: 'text',
  },
};

export const DEFAULT_TELEMETRY_ALERT_RULES: TelemetryAlertRules = {
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
  const apprise = asRecord(raw?.apprise);
  const mode = email?.mode;
  const textPolicy = apprise?.include_message_text;
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
      ...(typeof webhook?.url_redacted === 'string' ? { url_redacted: webhook.url_redacted } : {}),
      allow_private_network: webhook?.allow_private_network === true,
      private_network_grandfathered: webhook?.private_network_grandfathered === true,
    },
    apprise: {
      urls: typeof apprise?.urls === 'string' ? apprise.urls : '',
      allow_private_network: apprise?.allow_private_network === true,
      include_message_text:
        textPolicy === 'always' || textPolicy === 'never' ? textPolicy : 'public_only',
      format: apprise?.format === 'markdown' ? 'markdown' : 'text',
    },
  };
}

export function isEmailDestinationReady(email: NotificationEmailDest | undefined): boolean {
  return Boolean(email?.host?.trim() && email.to.trim());
}

export function isWebhookDestinationReady(webhook: NotificationWebhookDest | undefined): boolean {
  return Boolean(webhook?.url?.trim());
}

export function isAppriseDestinationReady(apprise: NotificationAppriseDest | undefined): boolean {
  return Boolean(apprise?.urls?.trim());
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
  const webhookUrl = secretForPatch(draft.webhook.url, current.webhook.url);
  const appriseUrls = secretForPatch(draft.apprise.urls, current.apprise.urls);
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
      ...(webhookUrl !== undefined ? { url: webhookUrl } : {}),
      allow_private_network: draft.webhook.allow_private_network,
      ...(hmacSecret !== undefined ? { hmac_secret: hmacSecret } : {}),
      ...(draft.webhook.private_network_grandfathered !==
      current.webhook.private_network_grandfathered
        ? { private_network_grandfathered: draft.webhook.private_network_grandfathered }
        : {}),
    },
    apprise: {
      allow_private_network: draft.apprise.allow_private_network,
      include_message_text: draft.apprise.include_message_text,
      format: draft.apprise.format,
      ...(appriseUrls !== undefined ? { urls: appriseUrls } : {}),
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

export type NotificationLanguage = 'auto' | 'fr' | 'en';
export type ShareShortenMode = 'auto' | 'always' | 'never';
export type SharePreviewPolicy = 'always' | 'private' | 'never';

/** `GET /api/share/status`: makes no request to the share service. */
export interface ShareStatus {
  enabled: boolean;
  host: string;
  hosts: string[];
  identity_ready: boolean;
  dm_encryption_available: boolean;
  banned: boolean;
  /** The share client's breaker is open: the service (and its terms pages) is not answering. */
  unreachable: boolean;
  shorten_mode: ShareShortenMode;
  preview_policy: SharePreviewPolicy;
}

/** `POST /api/share/links` answer. */
export interface ShareLink {
  id: string;
  url: string;
  expires_at: number;
}

/** `GET /api/share/{id}/meta` answer (links). */
export interface ShareMeta {
  kind: string;
  target_host: string | null;
  expires_at: number | null;
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
  /** Radio workarounds (Settings → Radio → Advanced). Absent from an older backend. */
  enable_message_poll_fallback?: boolean;
  force_channel_slot_reconfigure?: boolean;
  load_with_autoevict?: boolean;
  auto_clock_wraparound?: boolean;
  /** CORS allowlist (Settings → Local Configuration → Allowed origins). Empty: same-origin only. */
  auth_allowed_origins?: string[];
  /** Accept Web Push endpoints on a private network (self-hosted UnifiedPush). */
  push_allow_private_endpoints?: boolean;
  /** The upgrade turned push_allow_private_endpoints on: show a one-time notice. */
  push_private_endpoints_grandfathered?: boolean;
  /** The upgrade switched the opt-in off for a hostname it cannot place: show a review notice. */
  push_private_endpoints_review?: boolean;
  /** Seconds the radio may stay unreachable before radio_offline is sent (0 = at once). */
  radio_offline_grace_s?: number;
  /** Consent to the link and file sharing service. Off by default; never restored from a backup. */
  share_enabled?: boolean;
  share_shorten_mode?: ShareShortenMode;
  share_preview_policy?: SharePreviewPolicy;
  notification_language?: NotificationLanguage;
  directory_available?: boolean;
  /** Absent until the backend ships the field; UI falls back to defaults. */
  telemetry_alert_rules?: TelemetryAlertRules;
  notification_destinations?: NotificationDestinations;
}

/**
 * `GET /api/settings/view`: the part of `AppSettings` every role may read
 * (`app/models.py`, `AppSettingsView`). Everything else stays administrator-only.
 */
export type AppSettingsView = Pick<
  AppSettings,
  | 'ui_preferences'
  | 'last_message_times'
  | 'flood_scope'
  | 'known_regions'
  | 'blocked_keys'
  | 'blocked_names'
  | 'directory_available'
>;

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
  enable_message_poll_fallback?: boolean;
  force_channel_slot_reconfigure?: boolean;
  load_with_autoevict?: boolean;
  auto_clock_wraparound?: boolean;
  auth_allowed_origins?: string[];
  push_allow_private_endpoints?: boolean;
  push_private_endpoints_grandfathered?: boolean;
  push_private_endpoints_review?: boolean;
  radio_offline_grace_s?: number;
  share_enabled?: boolean;
  share_shorten_mode?: ShareShortenMode;
  share_preview_policy?: SharePreviewPolicy;
  notification_language?: NotificationLanguage;
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

/** `exact`: an advert position. `rounded`: about 1 km, never used for a distance. */
export type PositionPrecision = 'exact' | 'rounded';

export interface ObserverReachEntry {
  name: string;
  public_key?: string | null;
  lat?: number | null;
  lon?: number | null;
  /** Null without a position. A rounded position places the ear but makes no distance. */
  position_precision?: PositionPrecision | null;
  hops?: number | null;
  snr?: number | null;
  /** Often absent. Null or empty means the role is unknown, not that there is none. */
  role?: string | null;
  rssi?: number | null;
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

/**
 * A radio test flood the node just sent, so its reach can be listened for.
 *
 * The sent text is the server's business — this only carries what is needed to
 * follow the packet afterwards. `origin_*` is the node's own position at send
 * time; the reach endpoint has no Message row for this packet and so returns
 * neither an origin nor a distance of its own.
 */
export interface MeshTestResponse {
  packet_hash: string;
  sent_at: number;
  flood_scope: string;
  origin_lat: number | null;
  origin_lon: number | null;
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
  /** Secrets written as `********` instead of their value, by path (v1 download). */
  secret_omitted?: Record<string, boolean>;
}

export interface BackupRestoreResult {
  contacts_upserted: number;
  channels_upserted: number;
  settings_updated: boolean;
  groups_upserted: number;
  /** `code:value` strings (`secret_dropped_destination_changed:email.password`). */
  warnings: string[];
  private_key_warning: string;
}

/** Formats a backup file can carry (a file without `format` is v1). */
export const BACKUP_FORMATS = [
  'meshloom-backup-v1',
  'remoteterm-backup-v1',
  'meshloom-backup-v2',
  'meshloom-backup-v2-encrypted',
] as const;
export type BackupFormat = (typeof BACKUP_FORMATS)[number];

export type BackupIdentityRelation =
  | 'no_identity'
  | 'same'
  | 'restores_bound'
  | 'new_database'
  | 'replaces_live'
  | 'replaces_history'
  | 'needs_radio'
  | 'conflict';

export interface BackupIdentityRequires {
  replace_live_identity: boolean;
  wipe_history: boolean;
}

export interface BackupCounts {
  contacts: number;
  channels: number;
  groups: number;
  fanout_configs: number;
  push_subscriptions: number;
}

export interface BackupIntegrationSummary {
  name: string;
  type: string;
  enabled: boolean;
}

/** A push subscription the restore would write: notifications go to `host`. */
export interface BackupPushSummary {
  label: string;
  host: string;
}

export interface BackupInspectResult {
  format: string;
  encrypted: boolean;
  exported_at: number;
  meshloom_version: string | null;
  public_key: string | null;
  radio_name: string | null;
  has_private_key: boolean;
  private_key_status: string | null;
  has_radio_settings: boolean;
  /** A Wi-Fi block: credentials and/or the on/off flag. */
  has_wifi: boolean;
  /** The block holds credentials (else only the on/off flag). */
  has_wifi_credentials?: boolean;
  /** Those credentials are an open network (an SSID with no password). */
  wifi_open_network?: boolean;
  counts: BackupCounts;
  bot_names: string[];
  /** Bots are turned off on this server: the file's bots are not restored. */
  bots_will_be_skipped: boolean;
  /** Non-bot integrations in the file; enabled ones start right after a restore. */
  integrations: BackupIntegrationSummary[];
  /** The file's radio proxy setting (null: not in the file). */
  radio_proxy_enabled: boolean | null;
  /** Push subscriptions the restore would write; notifications start at once. */
  push_targets?: BackupPushSummary[];
  identity_relation: BackupIdentityRelation;
  identity_requires: BackupIdentityRequires;
  /** The history has no bound owner: kept unless the restore sends wipe_history. */
  identity_allows_wipe?: boolean;
  identity_refusal: string | null;
  live_radio_public_key: string | null;
  live_radio_name: string | null;
  ble_pin_differs_on_ble: boolean;
  wifi_ssid_differs_on_tcp: boolean;
  /** Reached over TCP and the file turns Wi-Fi off. */
  wifi_off_on_tcp?: boolean;
  /** The file's named sections (added by features such as plugins or accounts). */
  sections?: BackupSectionSummary[];
  warnings: string[];
}

export interface BackupSectionSummary {
  name: string;
  /** This version restores it (else it is ignored). */
  known: boolean;
  /** Written by a newer version of its owner: not restored. */
  too_new: boolean;
  /** Holds secrets (hashes included). */
  sensitive: boolean;
  /** What it holds, per language (en, fr); null when unknown. */
  description: Record<string, string> | null;
}

export interface BackupRestoreSteps {
  identity: boolean;
  meshloom: boolean;
  radio_settings: boolean;
  ble_pin: boolean;
  channels: boolean;
  contacts: boolean;
  wifi: boolean;
}

export interface BackupRestoreV2Request {
  confirm: boolean;
  backup: unknown;
  passphrase?: string;
  steps: BackupRestoreSteps;
  replace_live_identity: boolean;
  wipe_history: boolean;
  expected_live_public_key: string | null;
}

export type BackupRestoreStepName =
  'identity' | 'meshloom' | 'radio_settings' | 'channels' | 'contacts' | 'wifi';
export type BackupRestoreStepStatus =
  'pending' | 'running' | 'done' | 'skipped' | 'failed' | 'unsupported';

export interface BackupRestoreItem {
  name: string;
  status: BackupRestoreStepStatus;
  code: string | null;
}

export interface BackupRestoreStep {
  step: BackupRestoreStepName;
  status: BackupRestoreStepStatus;
  code: string | null;
  detail: string | null;
  items: BackupRestoreItem[];
}

export interface BackupRestoreV2Result {
  steps: BackupRestoreStep[];
  contacts_upserted: number;
  channels_upserted: number;
  groups_upserted: number;
  settings_updated: boolean;
  channels_on_radio: number;
  contacts_on_radio: number;
  identity_restored: boolean;
  reboot_required: boolean;
  warnings: string[];
}

export interface BackupRestoreJob {
  job_id: string;
  state: 'running' | 'done' | 'failed';
  started_at: number;
  finished_at: number | null;
  steps: BackupRestoreStep[];
  result: BackupRestoreV2Result | null;
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

/** The radio's own telemetry (`GET /api/radio/self-telemetry`). */
export interface RadioSelfTelemetry {
  sensors: LppSensor[];
  battery_mv: number | null;
  storage_used_kb: number | null;
  storage_total_kb: number | null;
}

export interface RadioFactoryResetResponse {
  status: 'reset_sent';
  message: string;
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
  apprise: boolean;
}

export type NotificationMediaChannel = keyof NotificationMediaFlags;

export const DEFAULT_NOTIFICATION_MEDIA: NotificationMediaFlags = {
  push: true,
  email: false,
  webhook: false,
  apprise: false,
};

export function resolveNotificationMedia(raw: unknown): NotificationMediaFlags {
  if (typeof raw === 'boolean') {
    return { push: raw, email: false, webhook: false, apprise: false };
  }
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    return {
      push: Boolean(obj.push),
      email: Boolean(obj.email),
      webhook: Boolean(obj.webhook),
      apprise: Boolean(obj.apprise),
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
  radio_offline: NotificationMediaFlags;
  radio_online: NotificationMediaFlags;
  app_started: NotificationMediaFlags;
}

export type PushDefaultsPatch = {
  [K in keyof PushDefaults]?: Partial<NotificationMediaFlags> | boolean;
};

export type ConversationMediaOverride = Partial<NotificationMediaFlags>;

export interface PushPreferences {
  defaults: PushDefaults;
  overrides: Record<string, boolean | ConversationMediaOverride>;
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
  /** stateKey -> whether the newest message was sent by this radio. */
  last_message_outgoing?: Record<string, boolean>;
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

/**
 * Local Meshloom Stats join state. Browser talks only to the Meshloom backend.
 * The Community addresses are fixed in the backend and never exposed.
 */
/**
 * `waiting_for_position`: Community is on, but the radio has no position it may
 * publish. Only its MQTT status leaves, so every Community surface treats it as
 * off until a position is chosen. Absent from an older backend.
 */
export type CommunityState = 'off' | 'active' | 'waiting_for_position';

/** Optional reason sent with an opt-out (`POST /v1/me/opt-out` upstream). */
export type CommunityOptOutReason = 'privacy' | 'not_useful' | 'technical_issue' | 'other';

/**
 * An opt-out Community did not confirm: Meshloom left, but Community may still
 * hold the position. Retried by hand only (`POST /api/community/opt-out/retry`).
 */
export interface CommunityOptOutPending {
  requested_at: string;
  reason?: CommunityOptOutReason | null;
}

export interface CommunityStatus {
  enabled: boolean;
  iata: string;
  publisher_configured: boolean;
  publisher_connected: boolean;
  state?: CommunityState;
  /**
   * Community banned this radio key (403 `banned`): writes, Live and the publisher are
   * stopped until a restart, a settings change or 12 h. Absent from an older backend.
   */
  banned?: boolean;
  /** Set while off after an opt-out Community did not confirm. Absent from an older backend. */
  opt_out_pending?: CommunityOptOutPending | null;
}

export interface CommunityUpdate {
  enabled?: boolean;
  iata?: string;
  /** Only with `enabled: false`. */
  opt_out_reason?: CommunityOptOutReason;
}

export interface CommunityIataBindRequest {
  /** No position: the backend sends the radio's own, rounded unless the radio broadcasts it. */
  iata: string;
  /** Join: bind with Community first, then turn Community on (only if the bind succeeds). */
  enable?: boolean;
}

export interface CommunityIataBindResult {
  iata: string;
  concordance: string;
  honored_for_buckets: boolean;
  distance_km: number | null;
  /** Precision of the bound position; null without one (or an older Community). */
  position_precision?: PositionPrecision | null;
}

export interface CommunityMeStats {
  unique_hashes_24h: number;
  unique_hashes_7d: number;
  iata: string;
  concordance: string;
  rank_in_iata: number | null;
  position_precision?: PositionPrecision | null;
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

/** Where an IATA code is, from the backend's bundled airport list (no request). */
export interface CommunityAirportHit {
  iata: string;
  lat: number;
  lon: number;
}

/** `GET /api/community/iata/suggest`: codes the IATA form proposes, never assigned. */
export interface CommunityIataSuggestion {
  /** The IATA most declared by active Community members nearby (Community only). */
  neighbors: string | null;
  /** The nearest airport (Community, or the bundled list when it does not answer). */
  nearest_airport: string | null;
  source: 'community' | 'local' | null;
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
  /** Compose install still runs the pre-4.18 host helper; re-run the installer. */
  legacy_update_helper?: boolean;
  /** Where `latest` came from: GitHub (primary), the Community mirror, or an env pin. */
  latest_source?: 'github' | 'community' | 'env' | null;
  job: OssUpdateJob;
}

export interface OssUpdateSettingsPatch {
  auto_update?: boolean;
  auto_update_window_start?: string;
  auto_update_window_end?: string;
  auto_update_weekdays?: number[];
}
