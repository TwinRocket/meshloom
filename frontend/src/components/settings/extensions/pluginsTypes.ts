import type { PluginInstance } from '../../../types';

/** One entry of `GET /api/plugins/bundled` (plugins shipped with this release). */
export interface BundledPlugin {
  plugin_id: string;
  version: string;
  name: Record<string, string>;
  summary: Record<string, string>;
  icon: string | null;
  categories: string[];
  hooks: string[];
  /** Declared permissions, by name (`messages.read` -> {channels, direct, ...}). */
  permissions: Record<string, Record<string, unknown>>;
  /** The manifest's privacy declaration (`sends_to_third_parties`, `stores_message_content`). */
  privacy: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  instances: number;
}

/** `GET /plugins/pause`: the global incident switch (`by` is a username). */
export interface PluginPauseState {
  paused: boolean;
  since: number | null;
  by: string | null;
}

export interface PluginLogs {
  lines: string[];
  truncated: boolean;
}

export interface PluginDiagnostic {
  plugin_id: string;
  plugin_version: string;
  kind: string;
  meshloom_version: string;
  python_version: string;
  isolation: string;
  status: string;
  last_error: string | null;
  restarts: number;
  dropped_events: number;
  granted_permissions: string[];
  logs: string[];
}

export interface PluginInstallRequest {
  kind: 'bundled' | 'custom';
  plugin_id: string;
  /** `owner/name` of a custom Store repository (`kind: 'custom'`). */
  repository?: string;
  /** The release version shown to the admin: refused when it is no longer the latest. */
  version?: string;
  label?: string;
  granted_permissions: Record<string, unknown>;
  config?: Record<string, unknown>;
  /** A local plugin's subscriptions. */
  hooks?: string[];
}

export interface PluginPatch {
  enabled?: boolean;
  label?: string;
  /** Plain fields replace the stored ones; a secret absent or masked is kept, `""` clears it. */
  config?: Record<string, unknown>;
  scope?: Record<string, unknown>;
  granted_permissions?: Record<string, unknown>;
  quotas?: Record<string, unknown>;
  /** Apply releases of the instance's source on their own (inside the update window). */
  auto_update?: boolean;
  sort_order?: number;
  /** A local plugin's subscriptions. */
  hooks?: string[];
}

export const LOCAL_HOOKS = [
  'message',
  'raw_packet',
  'contact',
  'telemetry',
  'health',
  'timer',
] as const;
export type LocalHook = (typeof LOCAL_HOOKS)[number];

export interface LocalPluginCreate {
  label: string;
  hooks: LocalHook[];
  permissions: Record<string, Record<string, unknown>>;
  template: 'blank' | 'echo';
}

/** One finding of the static scan of a local plugin's code (`PUT source`). */
export interface ScanReason {
  code: string;
  line: number;
  detail: string | null;
  informative: boolean;
}

/** A local-network device found in a local plugin's code (MSHL-342): a suggestion only. */
export interface LanHostSuggestion {
  /** The `lan_hosts` entry (no credentials, no default port). */
  host: string;
  /** The first source line that mentions it. */
  line: number;
  /** A known service port (Home Assistant 8123, radio proxy 5051, Meshloom 8000). */
  service_port: boolean;
}

export interface PluginSourceResult {
  instance: PluginInstance;
  scan: ScanReason[];
  http_hosts: string[];
  lan_hosts?: LanHostSuggestion[];
  lan_hosts_truncated?: boolean;
}

export interface CheckFinding {
  stage: string;
  code: string;
  message: string;
  path: string;
  line: number | null;
  informative: boolean;
}

export interface PluginCheckResult {
  ok: boolean;
  errors: CheckFinding[];
  warnings: CheckFinding[];
  http_hosts: string[];
  lan_hosts?: LanHostSuggestion[];
  lan_hosts_truncated?: boolean;
}

export interface PluginTestRequest {
  text: string;
  channel?: string;
  contact?: string;
}

export interface PluginTestResult {
  result: unknown;
  sends: Array<Record<string, unknown>>;
  truncated: boolean;
  logs: string[];
}

export type { PluginInstance };

/** The catalogue entry of a custom repository's last good release (MSHL-196). */
export interface CustomCatalogueEntry {
  tier: 'custom';
  signed: false;
  repository: string;
  plugin_id: string;
  version: string;
  tag: string;
  name: Record<string, string>;
  summary: Record<string, string>;
  permissions: Record<string, Record<string, unknown>>;
  privacy?: Record<string, unknown> | null;
  compat?: Record<string, unknown> | null;
  /** `compat.meshloom` admits this Meshloom; false: it would install as `incompatible`. */
  compatible: boolean;
  /** The release's `config.schema.json` ({} without one): the form asked before the install. */
  config_schema: Record<string, unknown>;
  archive_sha256: string | null;
  archive_size: number | null;
  /** `custom_unsigned`: the UI shows its own i18n copy of the warning. */
  warning: string;
  warning_text: Record<string, string>;
}

export interface StoreRepository {
  /** Lowercase `owner/name`, the key. */
  repository: string;
  /** `owner/name` as GitHub writes it. */
  display_name: string;
  added_at: number;
  checked_at: number | null;
  /** Error code of the last check, null when it succeeded. */
  last_error: string | null;
  entry: CustomCatalogueEntry | null;
  instances: StoreRepositoryInstance[];
  update_available: boolean;
}

/** Why an automatic update leaves an update for a manual one (null: it may run). */
export type AutoHold = 'consent_required' | 'config_required' | 'config_dropped';

/** An installed instance of a repository, with its pending update (as `GET /store/updates`). */
export interface StoreRepositoryInstance {
  instance_id: string;
  label: string;
  version: string;
  auto_update?: boolean;
  hold?: 'consent_required' | 'config_required' | null;
  auto_hold?: AutoHold | null;
  dropped_fields?: string[];
}

/** `GET /api/store/repositories`: makes no request to GitHub. */
export interface StoreRepositories {
  store_online: boolean;
  max_repositories: number;
  warning: string;
  warning_text: Record<string, string>;
  repositories: StoreRepository[];
}

/** `GET /api/store/status`: what the activation card says. Makes no request. */
export interface StoreStatus {
  enabled: boolean;
  /** Every host a Store request may reach (the server's own allowlist). */
  hosts: string[];
  sends: Array<'ip_address' | 'user_agent'>;
  user_agent: string;
  refresh_interval_s: number;
  missed_when_disabled: Array<'updates' | 'revocation_alerts'>;
  refresh_running: boolean;
  last_refresh_at: number | null;
}

/** `GET /api/store/repositories/{owner}/{name}/readme`: untrusted text, shown as plain text. */
export interface StoreReadme {
  lang: string | null;
  text: string;
  languages: string[];
}

export interface StoreRefreshResult {
  ran: boolean;
  last_refresh_at: number | null;
}

export interface PermissionDiff {
  added: string[];
  changed: string[];
  removed: string[];
  privacy_widened: boolean;
}

export interface SchemaDiff {
  added: string[];
  removed: string[];
  required_added: string[];
}

export interface PluginUpdateInstance {
  instance_id: string;
  label: string;
  version: string;
  auto_update: boolean;
  /** What the update would hold this instance for (null: it runs on). */
  hold: 'consent_required' | 'config_required' | null;
  /** The permissions it would ask consent for. */
  permissions: string[];
  /** New required config fields it lacks. */
  missing_fields: string[];
  /** Stored config fields the new schema rejects: the update drops and reports them. */
  dropped_fields: string[];
  /** Why an automatic update waits for a manual one (null: it may run). */
  auto_hold: AutoHold | null;
}

/** One entry of `GET /api/store/updates`. */
export interface PluginUpdate {
  plugin_id: string;
  kind: string;
  repository: string | null;
  from_version: string;
  to_version: string;
  name: Record<string, string>;
  /** In the requested language when the source has one; custom sources have none. */
  changelog: string | null;
  permissions: PermissionDiff;
  /** Null when the cached release predates the schema cache. */
  config_schema: SchemaDiff | null;
  instances: PluginUpdateInstance[];
}

export interface PluginUpdateRequest {
  version?: string;
  granted_permissions?: Record<string, unknown>;
  config?: Record<string, unknown>;
}
