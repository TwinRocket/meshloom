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
  kind: 'bundled';
  plugin_id: string;
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

export interface PluginSourceResult {
  instance: PluginInstance;
  scan: ScanReason[];
  http_hosts: string[];
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
