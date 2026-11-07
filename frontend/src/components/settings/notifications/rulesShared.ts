import type {
  NotificationChannel,
  NotificationEventInfo,
  NotificationEventRule,
  NotificationRules,
} from '../../../notifications/types';

export type ContactKind = 'companion' | 'repeater' | 'sensor';
export const CONTACT_KINDS: readonly ContactKind[] = ['companion', 'repeater', 'sensor'];

export type EventGroupId = 'messages' | 'nodes' | 'radio' | 'telemetry' | 'meshloom';

const GROUP_OF: Record<string, EventGroupId> = {
  'message.dm': 'messages',
  'message.channel': 'messages',
  'message.mention': 'messages',
  'contact.first_seen': 'nodes',
  'channel.found': 'nodes',
  'radio.offline': 'radio',
  'radio.online': 'radio',
  'telemetry.alert': 'telemetry',
};

export const GROUP_ORDER: readonly EventGroupId[] = [
  'messages',
  'nodes',
  'radio',
  'telemetry',
  'meshloom',
];

export function groupOf(eventId: string): EventGroupId {
  return GROUP_OF[eventId] ?? 'meshloom';
}

/** i18n key segment of an event id (`message.dm` -> `message_dm`). */
export function eventKey(eventId: string): string {
  return eventId.replace(/\./g, '_');
}

/** The configurable events, by group, in catalogue order. */
export function groupEvents(
  events: NotificationEventInfo[]
): Array<{ group: EventGroupId; events: NotificationEventInfo[] }> {
  const configurable = events.filter((e) => e.configurable);
  return GROUP_ORDER.map((group) => ({
    group,
    events: configurable.filter((e) => groupOf(e.id) === group),
  })).filter((g) => g.events.length > 0);
}

export function eventRule(rules: NotificationRules, eventId: string): NotificationEventRule {
  return rules.events[eventId] ?? { channels: [] };
}

/** Channels of a contact-type sub-row: its own list once `contact_types` exists, else the event's. */
export function kindChannels(rule: NotificationEventRule, kind: ContactKind): string[] {
  return rule.contact_types ? (rule.contact_types[kind] ?? []) : rule.channels;
}

/** `contact_types` with `kind` toggled for `channelId`; the other kinds keep what they resolve to. */
export function toggledContactTypes(
  rule: NotificationEventRule,
  kind: ContactKind,
  channelId: string,
  on: boolean
): Record<ContactKind, string[]> {
  return Object.fromEntries(
    CONTACT_KINDS.map((k) => {
      const current = kindChannels(rule, k);
      if (k !== kind) return [k, current];
      return [
        k,
        on ? [...new Set([...current, channelId])] : current.filter((c) => c !== channelId),
      ];
    })
  ) as Record<ContactKind, string[]>;
}

export function toggledChannels(current: string[], channelId: string, on: boolean): string[] {
  return on ? [...new Set([...current, channelId])] : current.filter((c) => c !== channelId);
}

export type ExceptionState = 'inherit' | 'on' | 'off';

export function exceptionState(map: Record<string, boolean>, channelId: string): ExceptionState {
  if (!(channelId in map)) return 'inherit';
  return map[channelId] ? 'on' : 'off';
}

/** inherit -> on -> off -> inherit. */
export function nextExceptionState(state: ExceptionState): ExceptionState {
  return state === 'inherit' ? 'on' : state === 'on' ? 'off' : 'inherit';
}

/** The map to PUT after `channelId` moved to `state`; `null` clears the whole exception. */
export function withExceptionState(
  map: Record<string, boolean>,
  channelId: string,
  state: ExceptionState
): Record<string, boolean> | null {
  const next = { ...map };
  if (state === 'inherit') delete next[channelId];
  else next[channelId] = state === 'on';
  return Object.keys(next).length === 0 ? null : next;
}

/** Exceptions naming no known channel are still listed (the server tolerates orphans). */
export function channelLabel(channels: NotificationChannel[], id: string): string {
  return channels.find((c) => c.id === id)?.name ?? id;
}

export type PauseChoice = '1h' | '8h' | 'tomorrow';

/** Epoch seconds a pause ends at; "tomorrow" is 08:00 local time of the next day. */
export function pauseUntil(choice: PauseChoice, nowMs: number): number {
  if (choice === '1h') return Math.floor(nowMs / 1000) + 3600;
  if (choice === '8h') return Math.floor(nowMs / 1000) + 8 * 3600;
  const d = new Date(nowMs);
  d.setDate(d.getDate() + 1);
  d.setHours(8, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

/** Seconds left of a pause, 0 once over or unset. */
export function pauseRemaining(pausedUntil: number | null, nowMs: number): number {
  if (pausedUntil == null) return 0;
  return Math.max(0, pausedUntil - Math.floor(nowMs / 1000));
}

export function formatRemaining(seconds: number): { hours: number; minutes: number } {
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  return { hours: Math.floor(minutes / 60), minutes: minutes % 60 };
}
