/** Types of the notifications API (`/api/notifications`, `app/routers/notifications.py`). */

import type { FanoutConfig } from '../types';

export type NotificationChannelType = 'web_push' | 'email' | 'webhook' | 'apprise';
export type NotificationIncludeText = 'always' | 'public_only' | 'never';
export type NotificationChannelStatusName = 'connected' | 'error' | 'paused' | 'disconnected';

/** One property of a type's JSON Schema, in the field kinds of the shared form subset. */
export interface NotificationSchemaProperty {
  type?: 'string' | 'integer' | 'boolean' | 'null';
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
  /** A preset (an Apprise service): `type` is then the preset id. */
  base_type?: string;
  /** A preset's one-paragraph how-to (i18n key) and the page of the service's own docs. */
  help_key?: string;
  wiki_url?: string;
  schema: { properties: Record<string, NotificationSchemaProperty>; required?: string[] };
  cloud: boolean;
  singleton: boolean;
  preset: boolean;
  default_limits: { per_min: number | null; per_hour: number | null; per_day: number | null };
  quiet_hours_exempt_default: boolean;
  include_message_text_default: NotificationIncludeText;
  /** The text policy cannot be changed (an SMS service cuts a long text). */
  include_message_text_locked?: boolean;
  /** The channel's body can be a template (Apprise and presets, ntfy, Discord). */
  templates?: boolean;
  /** The channel can send a heartbeat (ntfy, webhook, Apprise, Gotify, Home Assistant). */
  heartbeat?: boolean;
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
  /** Message body templates; `''` is the default body. */
  template_dm?: string;
  template_channel?: string;
  quiet_hours_exempt: boolean;
  rate_per_min: number | null;
  rate_per_hour: number | null;
  rate_per_day: number | null;
  /** Heartbeat (MSHL-131): 0 is off; the ping URL reads `********` when saved, `''` when not. */
  heartbeat_interval_s?: number;
  heartbeat_method?: 'GET' | 'POST';
  heartbeat_url?: string;
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

/** An active fanout Apprise row sending where a new channel sends (MSHL-134): never its URL. */
export interface FanoutDuplicate {
  id: string;
  name: string;
}

/** `POST /notifications/channels`: the channel, plus the fanout Apprise rows it duplicates. */
export interface NotificationChannelCreated extends NotificationChannel {
  fanout_duplicates?: FanoutDuplicate[];
}

/** `POST /fanout/{id}/convert-to-notification-channel` (MSHL-134). */
/** The convert answer: ids, names and state only, never a config (no URL or secret). */
export interface FanoutConversion {
  channel: Pick<NotificationChannel, 'id' | 'name' | 'type' | 'preset' | 'enabled'>;
  fanout: Pick<FanoutConfig, 'id' | 'name' | 'type' | 'enabled'> & { converted_to: string | null };
  already_converted: boolean;
  /** `include_outgoing_unsupported`, `private_channels_need_exception`,
   * `template_dm_dropped`, `template_channel_dropped`. */
  notes: string[];
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
  template_dm?: string;
  template_channel?: string;
  quiet_hours_exempt?: boolean | null;
  rate_per_min?: number | null;
  rate_per_hour?: number | null;
  rate_per_day?: number | null;
  heartbeat_interval_s?: number;
  heartbeat_method?: 'GET' | 'POST';
  /** Omitted or `********` keeps the saved URL, `''` removes it. */
  heartbeat_url?: string;
}

/** `POST /notifications/channels/preview-template`: the templates rendered on two sample messages. */
export interface NotificationTemplatePreviewRequest {
  template_dm: string;
  template_channel: string;
  markdown: boolean;
  include_message_text: NotificationIncludeText;
  language: 'auto' | 'fr' | 'en';
}

export interface NotificationTemplatePreview {
  dm: string;
  channel: string;
  /** The variables a template may use (`{name}`). */
  variables: string[];
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
  /** Keyword rules (`message.keyword`, at most 20). */
  keywords: NotificationKeywordRule[];
  /** Watched nodes (`contact.node_seen` / `contact.node_back`, at most 50). */
  watched_nodes: NotificationWatchedNode[];
  radio_offline_grace_s: number;
  mention_whole_word: boolean;
  /** The `notification_event` WebSocket frame for the Feed (default on; no UI yet). */
  emit_ws_events?: boolean;
  /** Quiet hours: HH:MM window in the host time zone, ISO weekdays (Monday = 0). */
  quiet_hours?: NotificationQuietHours;
  push_allow_private_endpoints: boolean;
  /** Read-only. */
  host_timezone: string;
}

export interface NotificationQuietHours {
  enabled: boolean;
  start: string;
  end: string;
  weekdays: number[];
  digest_at_end: boolean;
}

/** A keyword rule (`app/notifications/keywords.py`): no regex, 64 characters at most. */
export interface NotificationKeywordRule {
  id: string;
  pattern: string;
  match: 'contains' | 'word';
  /** Every conversation, the direct messages, or these conversation keys (`channel-<key>`). */
  scope: 'all' | 'dms' | string[];
  channels: string[];
  cooldown_s: number;
}

/** A watched node (`app/notifications/watched_nodes.py`): at most 50. */
export interface NotificationWatchedNode {
  /** 64 lowercase hex characters. */
  public_key: string;
  channels: string[];
  /** Quiet period after a notification for this node. */
  cooldown_s: number;
  /** A sighting after this much silence is `contact.node_back`. */
  silence_s: number;
}

export type NotificationKeywordInput = Omit<NotificationKeywordRule, 'id'> & { id?: string };

export interface NotificationRulesPatch {
  quiet_hours?: Partial<NotificationQuietHours>;
  events?: Record<string, Partial<NotificationEventRule>>;
  /** Epoch seconds; `null` resumes. */
  paused_until?: number | null;
  radio_offline_grace_s?: number;
  mention_whole_word?: boolean;
  emit_ws_events?: boolean;
  /** The whole list; a rule without an `id` gets one from the server. */
  keywords?: NotificationKeywordInput[];
  /** The whole list of watched nodes. */
  watched_nodes?: NotificationWatchedNode[];
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
  /** Exact figures of the last 24 hours (absent from a server older than this field). */
  totals?: { sent: number; suppressed_quiet: number; aggregated: number };
}

/** The admin-only `notification_channel_changed` WebSocket event. */
export interface NotificationChannelChange {
  id: string;
  action: 'created' | 'changed' | 'enabled' | 'disabled' | 'deleted';
  type: string;
  name: string;
}

/** One row of `GET /api/push/my-rules`: the admin default and the account's own choice. */
export interface MyPushRuleState {
  /** Whether the admin defaults send this event to Web Push. */
  default: boolean;
  /** The account's choice for its own devices; null follows the default. */
  choice: boolean | null;
}

/** The caller's own Web Push rules (MSHL-321), overriding the admin defaults. */
export interface MyPushRules {
  events: Record<string, MyPushRuleState>;
  contact_types: Record<string, MyPushRuleState>;
  /** The account's own exception per conversation (`channel-<key>`, `contact-<pubkey>`), MSHL-362. */
  conversations: Record<string, boolean>;
  /** The account's own pause (MSHL-363): `until` is epoch seconds, null until it resumes. */
  pause: MyPushPause;
}

export interface MyPushPause {
  active: boolean;
  until: number | null;
}

export interface MyPushRulesPatch {
  /** `null` goes back to the default. */
  events?: Record<string, boolean | null>;
  contact_types?: Record<string, boolean | null>;
  /** `null` removes the exception. */
  conversations?: Record<string, boolean | null>;
}
