import {
  Bell,
  BellDot,
  BellRing,
  Hash,
  House,
  Mail,
  MessageCircle,
  MessagesSquare,
  Rocket,
  Send,
  ShieldCheck,
  Smartphone,
  Webhook,
  type LucideIcon,
} from 'lucide-react';

import { ApiError, getApiErrorCode } from '../../../api';
import type {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationEventInfo,
  NotificationRules,
  NotificationRulesPatch,
  NotificationTypeInfo,
} from '../../../notifications/types';
import { BUILTIN_PUSH_ID } from '../../../notifications/ids';
import type { FieldErrors } from '../../forms/schemaFormModel';

/** What a secret reads as on the wire (it is never filled in a form). */
export const SECRET_MASK = '********';

export const TYPE_ICONS: Record<string, LucideIcon> = {
  web_push: Bell,
  email: Mail,
  webhook: Webhook,
  apprise: BellRing,
  telegram: Send,
  gotify: BellRing,
  home_assistant: House,
  discord: MessageCircle,
  pushover: BellDot,
  matrix: Hash,
  signal: ShieldCheck,
  slack: MessagesSquare,
  mattermost: MessagesSquare,
  rocketchat: Rocket,
  sms_free: Smartphone,
  sms_sfr: Smartphone,
};

export function iconFor(type: string): LucideIcon {
  return TYPE_ICONS[type] ?? BellRing;
}

/** The status dot of a channel (`connected`, `error`, `paused`, `disconnected`). */
export const STATUS_DOT_CLASS: Record<string, string> = {
  connected: 'bg-success',
  error: 'bg-destructive',
  paused: 'bg-warning',
  disconnected: 'bg-muted-foreground/50',
};

/** Consecutive failures after which a channel is flagged (the server's `FAILED_EVENT_AFTER`). */
export const FAILING_AFTER = 3;

export type ChannelAttention = 'secret_unreadable' | 'paused' | 'failing';

/**
 * Why a channel needs a look (MSHL-130), or `null`: a stored secret the key cannot open, an
 * auto-pause after repeated failures, or `FAILING_AFTER` failures in a row.
 */
export function channelAttention(channel: NotificationChannel): ChannelAttention | null {
  if (channel.secret_unreadable) return 'secret_unreadable';
  if (!channel.enabled) return null;
  if (channel.status.status === 'paused') return 'paused';
  if (channel.failure_count >= FAILING_AFTER) return 'failing';
  return null;
}

/** `legacy:*` channels still decided by the 4.18.x settings (MSHL-112). */
export function isLegacyChannel(id: string): boolean {
  return id.startsWith('legacy:');
}

/** The type id the API wants for a registry entry: a preset rides on its base type. */
export function createTypeOf(entry: NotificationTypeInfo): { type: string; preset: string | null } {
  if (entry.preset) return { type: entry.base_type ?? 'apprise', preset: entry.type };
  return { type: entry.type, preset: null };
}

/** The registry entry of a stored channel (a preset channel finds its preset first). */
export function entryOfChannel(
  types: NotificationTypeInfo[],
  channel: Pick<NotificationChannel, 'type' | 'preset'>
): NotificationTypeInfo | undefined {
  return (
    (channel.preset ? types.find((e) => e.preset && e.type === channel.preset) : undefined) ??
    types.find((e) => !e.preset && e.type === channel.type)
  );
}

/** i18n key prefix of a type's texts and, through `i18nKeyResolver`, its fields. */
export function typePrefix(type: string): string {
  return `settings.notifications.types.${type}`;
}

/** The i18n keys the server attaches to a field (`label_key`, `help_key`), by field name. */
export function fieldTextKeys(
  schema: NotificationTypeInfo['schema']
): Record<string, { label?: string; help?: string }> {
  return Object.fromEntries(
    Object.entries(schema.properties ?? {}).map(([name, p]) => [
      name,
      { label: p.label_key, help: p.help_key },
    ])
  );
}

/**
 * A schema in the subset of the shared form: the fields the server marks `hidden` (banner flags,
 * not settings) are dropped and its `label_key` / `help_key` extensions are removed (the form
 * rejects unknown keywords; `fieldTextKeys` carries them to the label resolver).
 */
export function visibleSchema(
  schema: NotificationTypeInfo['schema']
): NotificationTypeInfo['schema'] {
  const properties = Object.fromEntries(
    Object.entries(schema.properties ?? {})
      .filter(([, p]) => !p.hidden)
      .map(([name, p]) => {
        const { label_key: _label, help_key: _help, ...rest } = p;
        return [name, rest];
      })
  );
  return { ...schema, properties };
}

/** Names of the secret fields of a schema (`writeOnly`, directly or inside a nullable `anyOf`). */
export function secretNames(schema: NotificationTypeInfo['schema']): string[] {
  return Object.entries(schema.properties ?? {})
    .filter(([, p]) => p.writeOnly === true || p.anyOf?.some((b) => b.writeOnly === true))
    .map(([name]) => name);
}

export type ReceivePreset = 'like_push' | 'alerts_only' | 'everything';

/** The events "Alerts only" keeps: the radio, telemetry, mentions and channel changes. */
export const ALERT_EVENTS = [
  'radio.offline',
  'radio.online',
  'telemetry.alert',
  'message.mention',
  'notify.channel_changed',
] as const;

/**
 * The rules PATCH that adds `channelId` to the events of a preset: the ones `builtin:push`
 * receives (Like Push), the alert events, or every configurable one. Nothing is removed.
 */
export function receivePatch(
  preset: ReceivePreset,
  channelId: string,
  rules: NotificationRules,
  events: NotificationEventInfo[]
): NotificationRulesPatch {
  const configurable = events.filter((e) => e.configurable);
  const wanted = configurable.filter((e) => {
    if (preset === 'everything') return true;
    if (preset === 'alerts_only') return (ALERT_EVENTS as readonly string[]).includes(e.id);
    return (rules.events[e.id]?.channels ?? []).includes(BUILTIN_PUSH_ID);
  });
  const patch: NotificationRulesPatch['events'] = {};
  for (const event of wanted) {
    const current = rules.events[event.id]?.channels ?? [];
    if (!current.includes(channelId)) patch[event.id] = { channels: [...current, channelId] };
  }
  return { events: patch };
}

/** The error code of a failed call, when the server sent one (`{code, params}`). */
export function errorCode(err: unknown): string | null {
  return err instanceof ApiError ? (getApiErrorCode(err.detail)?.code ?? null) : null;
}

/** Fields named by an `invalid_config` answer (`params.fields`), for the form to flag. */
export function fieldErrorsOf(err: unknown, message: string): FieldErrors {
  if (!(err instanceof ApiError)) return {};
  const parsed = getApiErrorCode(err.detail);
  const fields = parsed?.params.fields;
  if (!Array.isArray(fields)) return {};
  return Object.fromEntries(
    fields
      .map((f) => String(f).replace(/^config\./, ''))
      .filter(Boolean)
      .map((name) => [name, [message]])
  );
}

export interface ChannelDraft {
  name: string;
  language: 'auto' | 'fr' | 'en';
  include_message_text: 'always' | 'public_only' | 'never';
  allow_private_network: boolean;
  verify_tls: boolean;
  /** Message body templates as typed; '' is the default body. */
  template_dm: string;
  template_channel: string;
  /** Send limits as typed: '' keeps the type default, otherwise a positive integer. */
  rate_per_min: string;
  rate_per_hour: string;
  rate_per_day: string;
  quiet_hours_exempt: boolean;
  /** Digest mode (MSHL-136): 0 is off, else a summary every N seconds. */
  digest_interval_s: number;
  /** Heartbeat: 0 is off. The URL as typed: '' keeps the saved one unless cleared. */
  heartbeat_interval_s: number;
  heartbeat_method: 'GET' | 'POST';
  heartbeat_url: string;
  heartbeat_url_clear: boolean;
}

/** `''` is "use the default" (sent as null); anything else is the integer the server validates. */
export function parseLimit(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** The limit and quiet-hours part of a create or patch body. */
export function advancedBody(draft: ChannelDraft) {
  return {
    rate_per_min: parseLimit(draft.rate_per_min),
    rate_per_hour: parseLimit(draft.rate_per_hour),
    rate_per_day: parseLimit(draft.rate_per_day),
    quiet_hours_exempt: draft.quiet_hours_exempt,
    digest_interval_s: draft.digest_interval_s,
  };
}

/** The templates of a create or patch body; a type without a text body sends none. */
export function templateBody(entry: NotificationTypeInfo, draft: ChannelDraft) {
  return entry.templates
    ? { template_dm: draft.template_dm, template_channel: draft.template_channel }
    : {};
}

/** The heartbeat of a create or patch body; a type without heartbeat sends none. */
export function heartbeatBody(entry: NotificationTypeInfo, draft: ChannelDraft) {
  if (!entry.heartbeat) return {};
  const url = draft.heartbeat_url.trim();
  return {
    heartbeat_interval_s: draft.heartbeat_interval_s,
    heartbeat_method: draft.heartbeat_method,
    ...(url ? { heartbeat_url: url } : draft.heartbeat_url_clear ? { heartbeat_url: '' } : {}),
  };
}

export function createBody(
  entry: NotificationTypeInfo,
  draft: ChannelDraft,
  config: Record<string, unknown>
): NotificationChannelCreate {
  const { type, preset } = createTypeOf(entry);
  return {
    type,
    preset,
    name: draft.name.trim(),
    language: draft.language,
    config,
    allow_private_network: draft.allow_private_network,
    include_message_text: entry.cloud ? draft.include_message_text : null,
    verify_tls: draft.verify_tls,
    ...templateBody(entry, draft),
    ...advancedBody(draft),
    ...heartbeatBody(entry, draft),
  };
}
