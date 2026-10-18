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
}

export interface PluginPatch {
  enabled?: boolean;
  label?: string;
  scope?: Record<string, unknown>;
  granted_permissions?: Record<string, unknown>;
  quotas?: Record<string, unknown>;
  sort_order?: number;
}

export type { PluginInstance };
