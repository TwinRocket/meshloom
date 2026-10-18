/** Types of the notifications API (`/api/notifications`, `app/routers/notifications.py`). */

export type NotificationChannelType = 'web_push' | 'email' | 'webhook' | 'apprise';
export type NotificationIncludeText = 'always' | 'public_only' | 'never';
export type NotificationChannelStatusName = 'connected' | 'error' | 'paused' | 'disconnected';

/** One property of a type's JSON Schema, in the field kinds of the shared form subset. */
export interface NotificationSchemaProperty {
  type?: 'string' | 'integer' | 'boolean';
  anyOf?: NotificationSchemaProperty[];
  enum?: string[];
  default?: unknown;
  /** The single marker of a secret field: it is sent, never returned. */
  writeOnly?: boolean;
  label_key?: string;
  help_key?: string;
  title?: string;
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  readOnly?: boolean;
  hidden?: boolean;
}

export interface NotificationTypeInfo {
  type: NotificationChannelType | string;
  schema: { properties: Record<string, NotificationSchemaProperty>; required?: string[] };
  cloud: boolean;
  singleton: boolean;
  preset: boolean;
  default_limits: { per_min: number | null; per_hour: number | null; per_day: number | null };
  quiet_hours_exempt_default: boolean;
  include_message_text_default: NotificationIncludeText;
}

export interface NotificationChannel {
  id: string;
  type: NotificationChannelType | string;
  preset: string | null;
  name: string;
  enabled: boolean;
  language: 'auto' | 'fr' | 'en';
  /** Secret fields read `********` when they hold a value and `''` when they do not. */
  config: Record<string, unknown>;
  secret_fields: string[];
  secret_unreadable: boolean;
  allow_private_network: boolean;
  include_message_text: NotificationIncludeText;
  verify_tls: boolean;
  quiet_hours_exempt: boolean;
  rate_per_min: number | null;
  rate_per_hour: number | null;
  rate_per_day: number | null;
  limits: { per_min: number | null; per_hour: number | null; per_day: number | null };
  sort_order: number;
  created_at: number;
  paused_until: number | null;
  failure_count: number;
  deletable: boolean;
  status: {
    status: NotificationChannelStatusName | string;
    last_error: string | null;
    last_ok_at: number | null;
  };
}

/** The body of a creation, and of a test before saving. Secrets go in `config`. */
export interface NotificationChannelCreate {
  type: string;
  name: string;
  id?: string;
  preset?: string | null;
  enabled?: boolean;
  language?: 'auto' | 'fr' | 'en';
  config?: Record<string, unknown>;
  allow_private_network?: boolean;
  include_message_text?: NotificationIncludeText | null;
  verify_tls?: boolean;
  quiet_hours_exempt?: boolean | null;
  rate_per_min?: number | null;
  rate_per_hour?: number | null;
  rate_per_day?: number | null;
}

/** Omit a secret (or send `null` / `********`) to keep it; `''` clears it. */
export type NotificationChannelPatch = Partial<Omit<NotificationChannelCreate, 'id' | 'preset'>>;

export interface NotificationEventRule {
  channels: string[];
  always_deliver?: boolean;
  contact_types?: Partial<Record<'companion' | 'repeater' | 'sensor', string[]>>;
}

export interface NotificationRules {
  version: number;
  /** Epoch seconds while the global pause holds, else null. */
  paused_until: number | null;
  events: Record<string, NotificationEventRule>;
  /** `{conversation key: {channel id: on/off}}`. */
  conversations: Record<string, Record<string, boolean>>;
  keywords: unknown[];
  watched_nodes: unknown[];
  radio_offline_grace_s: number;
  mention_whole_word: boolean;
  push_allow_private_endpoints: boolean;
  /** Read-only. */
  host_timezone: string;
}

export interface NotificationRulesPatch {
  events?: Record<string, Partial<NotificationEventRule>>;
  /** Epoch seconds; `null` resumes. */
  paused_until?: number | null;
  radio_offline_grace_s?: number;
  mention_whole_word?: boolean;
  push_allow_private_endpoints?: boolean;
}

export interface NotificationEventInfo {
  id: string;
  urgent: boolean;
  configurable: boolean;
  locked_channels: string[];
}

export interface NotificationDelivery {
  id: number;
  channel_id: string;
  event: string;
  dedup_key: string;
  status: string;
  error: string | null;
  attempts: number;
  created_at: number;
}

export interface NotificationDeliveries {
  deliveries: NotificationDelivery[];
  counters: { channel_id: string; hour: number; sent: number }[];
}

/** The admin-only `notification_channel_changed` WebSocket event. */
export interface NotificationChannelChange {
  id: string;
  action: 'created' | 'changed' | 'enabled' | 'disabled' | 'deleted';
  type: string;
  name: string;
}
