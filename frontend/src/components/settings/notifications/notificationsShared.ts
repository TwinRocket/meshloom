import {
  Bell,
  BellRing,
  House,
  Mail,
  MessageCircle,
  Send,
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
};

export function iconFor(type: string): LucideIcon {
  return TYPE_ICONS[type] ?? BellRing;
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
  };
}
