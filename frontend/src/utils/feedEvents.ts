import type { Channel, Contact, HealthStatus } from '../types';
import type { NotificationEventFrame } from '../wsEvents';
import { getServerClockOffsetSec } from '../stores/rawPacketStore';
import { isFeedStoreActive, recordFeedEvent } from '../stores/feedStore';

/**
 * Event rows of the feed, session-only, from two sources that share one set of keys:
 *
 * - the `notification_event` frame (`app/notifications/ws_frame.py`, `noteNotificationEvent`),
 *   for the four catalogue ids of `NOTIFICATION_EVENT_IDS` and nothing else;
 * - the client reducers over the frames it already receives (`health`, `contact`, `channel`),
 *   for what the dispatcher does not emit: `contact.renamed` (feed-only), and every catalogue
 *   event no enabled channel routes, or all of them when `rules.emit_ws_events` is off. The
 *   dispatcher frames only events that matched an enabled channel.
 *
 * Whichever source reports first records the row; the other one adds nothing (shared outage
 * state, contact memory and keys). The rows and their sentences are the same either way.
 *
 * This module is the only place of the feed that knows the event ids. They are the
 * Notifications catalogue's (`app/notifications/events.py`, `EVENT_IDS`);
 * `tests/test_feed_event_ids.py` fails when one of `NOTIFICATION_EVENT_IDS` leaves the
 * catalogue. `radio.offline` is only reported after the Notifications grace
 * (`rules.radio_offline_grace_s`) of an unreachable radio: `setRadioOfflineGraceS` carries the
 * configured value, which only an administrator can read (the rules API is admin-only); every other
 * session keeps the default, `RADIO_OFFLINE_GRACE_MS` (60 s).
 */

/** Ids shared with the Notifications catalogue. */
export const NOTIFICATION_EVENT_IDS = [
  'radio.offline',
  'radio.online',
  'contact.first_seen',
  'channel.found',
] as const;

/** Ids only the feed has (not notifications): flagged so nobody mistakes them for the catalogue. */
export const FEED_ONLY_EVENT_IDS = ['contact.renamed'] as const;

export type NotificationEventId = (typeof NOTIFICATION_EVENT_IDS)[number];
export type FeedOnlyEventId = (typeof FEED_ONLY_EVENT_IDS)[number];
export type FeedEventId = NotificationEventId | FeedOnlyEventId;

/** Default of `rules.radio_offline_grace_s` (`app/models.py`), used when the rule is not readable. */
export const RADIO_OFFLINE_GRACE_MS = 60_000;

let graceMs = RADIO_OFFLINE_GRACE_MS;

/** The configured grace in seconds (0 = at once); anything invalid restores the default. */
export function setRadioOfflineGraceS(seconds: number | null | undefined): void {
  graceMs =
    typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
      ? Math.min(seconds, 3600) * 1000
      : RADIO_OFFLINE_GRACE_MS;
}

interface FeedEventBase {
  /** Unique and stable: a frame replayed with the same key adds no row. */
  key: string;
  /** Server-clock seconds (what messages use), so rows interleave by one clock. */
  at: number;
  /** Browser `Date.now()` when the row was produced: the follow counter compares with it. */
  atMs: number;
}

export type FeedEvent =
  | (FeedEventBase & { id: 'radio.offline' })
  | (FeedEventBase & { id: 'radio.online'; downtimeS: number })
  | (FeedEventBase & {
      id: 'contact.first_seen';
      publicKey: string;
      name: string | null;
      contactType: number;
    })
  | (FeedEventBase & {
      id: 'contact.renamed';
      feedOnly: true;
      publicKey: string;
      from: string;
      to: string;
    })
  | (FeedEventBase & { id: 'channel.found'; channelKey: string; name: string });

export function isFeedOnlyEvent(event: FeedEvent): boolean {
  return (FEED_ONLY_EVENT_IDS as readonly string[]).includes(event.id);
}

type RadioState = NonNullable<HealthStatus['radio_state']>;

/** Browser milliseconds to the server clock in seconds, by the measured skew ("Clocks"). */
function serverSeconds(ms: number): number {
  return Math.floor(ms / 1000 - getServerClockOffsetSec());
}

function radioStateOf(health: HealthStatus): RadioState {
  return (
    health.radio_state ??
    (health.radio_initializing
      ? 'initializing'
      : health.radio_connected
        ? 'connected'
        : 'disconnected')
  );
}

// ---- radio.offline / radio.online ----------------------------------------------------------

let lastRadioState: RadioState | null = null;
let outageStartedMs: number | null = null;
let offlineTimer: ReturnType<typeof setTimeout> | null = null;
/** Start of an outage whose `radio.offline` row was recorded: only then is there an `online`. */
let notifiedOutageMs: number | null = null;

function clearOfflineTimer(): void {
  if (offlineTimer !== null) clearTimeout(offlineTimer);
  offlineTimer = null;
}

function fireOffline(startedMs: number): void {
  offlineTimer = null;
  if (outageStartedMs !== startedMs) return;
  const recorded = recordFeedEvent({
    key: `radio.offline:${startedMs}`,
    id: 'radio.offline',
    at: serverSeconds(startedMs),
    atMs: startedMs,
  });
  if (recorded) notifiedOutageMs = startedMs;
}

/**
 * Feed one `health` frame. States that mean "unreachable" (`disconnected`, `connecting`) start
 * or continue an outage, which becomes a row after the grace delay, still measured from its
 * start. `rebooting` (Meshloom's own expected reboot, `EXPECTED_REBOOT_GRACE_SECONDS`),
 * `paused` and the identity states are expected: they end a pending outage without a row.
 * `connected` after a reported outage records `radio.online`; after a shorter flap, nothing.
 * The first frame only sets the baseline: an outage already running when the page loaded is not
 * reported (events are session-only).
 */
export function noteRadioHealth(health: HealthStatus, nowMs: number = Date.now()): void {
  const state = radioStateOf(health);
  const previous = lastRadioState;
  lastRadioState = state;
  if (previous === null) return;

  if (state === 'connected') {
    clearOfflineTimer();
    const reported = notifiedOutageMs;
    outageStartedMs = null;
    notifiedOutageMs = null;
    if (reported !== null) {
      recordFeedEvent({
        key: `radio.online:${reported}`,
        id: 'radio.online',
        at: serverSeconds(nowMs),
        atMs: nowMs,
        downtimeS: Math.max(0, Math.round((nowMs - reported) / 1000)),
      });
    }
    return;
  }

  if (state === 'disconnected' || state === 'connecting') {
    // `connecting` right after `disconnected` is the same outage, not a new one.
    if (outageStartedMs !== null || !isFeedStoreActive()) return;
    const startedMs = nowMs;
    outageStartedMs = startedMs;
    offlineTimer = setTimeout(() => fireOffline(startedMs), graceMs);
    return;
  }

  if (state === 'initializing') return;

  // rebooting, paused, identity_*: expected, never an outage.
  clearOfflineTimer();
  outageStartedMs = null;
}

// ---- contact.first_seen / contact.renamed ---------------------------------------------------

/** Name last seen per contact key, from `contact` frames. */
const seenContacts = new Map<string, string | null>();

export interface ContactFrameContext {
  /** The contact as the client held it before this frame, if any. */
  previous: Contact | undefined;
  /**
   * True while the contact list is not loaded or a post-reconnect resync is in flight
   * (`activeSnapshotsRef` in `useRealtimeAppState`): the frames are snapshots there, not events.
   */
  resyncing: boolean;
}

/**
 * Feed one `contact` frame. A `contact` frame is a state snapshot (one per `last_seen` update,
 * a burst after a reconnect), so only a key the client never held is a first sighting and only
 * a changed non-empty name is a rename. Frames during a resync are remembered, never reported.
 */
/**
 * A contact this tab is creating by hand: its `contact` frame can arrive before the REST answer
 * and is not "heard" on the air. Call it before the request. (Another tab or browser cannot tell.)
 */
export function noteLocalContact(publicKey: string): void {
  if (!seenContacts.has(publicKey)) seenContacts.set(publicKey, null);
}

/** Contact types Notifications never announces as first seen (`app/push/first_seen.py`). */
const SILENT_FIRST_SEEN_TYPES: readonly number[] = [0, 3];

export function noteContactFrame(
  contact: Contact,
  { previous, resyncing }: ContactFrameContext,
  nowMs: number = Date.now()
): void {
  const key = contact.public_key;
  const known = seenContacts.has(key) || previous !== undefined;
  const lastName = seenContacts.has(key) ? seenContacts.get(key) : (previous?.name ?? null);
  seenContacts.set(key, contact.name ?? lastName ?? null);
  if (resyncing) return;

  if (!known) {
    // Same exclusions as the catalogue's `contact.first_seen`: unknown and room contacts.
    if (SILENT_FIRST_SEEN_TYPES.includes(contact.type)) return;
    // `first_seen` is the server's own stamp of the first RF sighting.
    const at = contact.first_seen ?? serverSeconds(nowMs);
    recordFeedEvent({
      key: `contact.first_seen:${key}:${at}`,
      id: 'contact.first_seen',
      at,
      atMs: nowMs,
      publicKey: key,
      name: contact.name,
      contactType: contact.type,
    });
    return;
  }

  const to = contact.name?.trim();
  const from = lastName?.trim();
  if (to && from && to !== from) {
    recordFeedEvent({
      key: `contact.renamed:${key}:${to}:${nowMs}`,
      id: 'contact.renamed',
      feedOnly: true,
      at: serverSeconds(nowMs),
      atMs: nowMs,
      publicKey: key,
      from,
      to,
    });
  }
}

/** A deleted contact is new again if it reappears. */
export function forgetContact(publicKey: string): void {
  seenContacts.delete(publicKey);
}

// ---- channel.found --------------------------------------------------------------------------

/**
 * Feed one `channel` frame: a pending channel the client did not hold is a discovery (what the
 * "discovered" toast reports).
 */
export function noteChannelFrame(
  channel: Channel,
  existed: boolean,
  nowMs: number = Date.now()
): void {
  if (existed || channel.membership !== 'pending') return;
  recordFeedEvent({
    key: `channel.found:${channel.key}`,
    id: 'channel.found',
    at: serverSeconds(nowMs),
    atMs: nowMs,
    channelKey: channel.key,
    name: channel.name,
  });
}

// ---- notification_event frames ---------------------------------------------------------------

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/**
 * Feed one `notification_event` frame. Only the four ids of `NOTIFICATION_EVENT_IDS` make a
 * row; every other id (`message.*`, `notify.*`, telemetry...) is ignored, so this source can
 * never show a conversation, whatever the session's role. A frame and the matching reducer
 * share their memory, so an event both report is one row.
 */
export function noteNotificationEvent(
  frame: NotificationEventFrame,
  nowMs: number = Date.now()
): void {
  // Never a conversation's event (the server already keeps those from a kiosk session).
  if (frame.conversation_type === 'PRIV') return;
  const data = frame.data ?? {};
  const occurredAt = finiteNumber(frame.occurred_at);
  switch (frame.event) {
    case 'radio.offline': {
      // Only an outage this page saw start (the `health` frames), like the reducer: the frame
      // ends its grace with the server's configured delay instead of the session's default.
      const startedMs = outageStartedMs;
      if (startedMs === null || notifiedOutageMs !== null) return;
      clearOfflineTimer();
      fireOffline(startedMs);
      return;
    }
    case 'radio.online': {
      // Like the reducer: no `online` row without the `offline` one of this session.
      const reported = notifiedOutageMs;
      if (reported === null) return;
      clearOfflineTimer();
      outageStartedMs = null;
      notifiedOutageMs = null;
      const downtimeS = finiteNumber(data.downtime_s);
      recordFeedEvent({
        key: `radio.online:${reported}`,
        id: 'radio.online',
        at: occurredAt ?? serverSeconds(nowMs),
        atMs: nowMs,
        downtimeS: Math.max(
          0,
          Math.round(downtimeS !== null ? downtimeS : (nowMs - reported) / 1000)
        ),
      });
      return;
    }
    case 'contact.first_seen': {
      const publicKey = text(data.public_key);
      const contactType = finiteNumber(data.contact_type) ?? 0;
      if (!publicKey || SILENT_FIRST_SEEN_TYPES.includes(contactType)) return;
      if (seenContacts.has(publicKey)) return;
      const name = text(data.name) || null;
      seenContacts.set(publicKey, name);
      const at = occurredAt ?? serverSeconds(nowMs);
      recordFeedEvent({
        key: `contact.first_seen:${publicKey}:${at}`,
        id: 'contact.first_seen',
        at,
        atMs: nowMs,
        publicKey,
        name,
        contactType,
      });
      return;
    }
    case 'channel.found': {
      const channelKey = text(data.channel_key);
      if (!channelKey) return;
      recordFeedEvent({
        key: `channel.found:${channelKey}`,
        id: 'channel.found',
        at: occurredAt ?? serverSeconds(nowMs),
        atMs: nowMs,
        channelKey,
        name: text(data.name),
      });
      return;
    }
    default:
      return;
  }
}

/** Forget every timer and memory (tests, and a store reset). */
export function resetFeedEvents(): void {
  clearOfflineTimer();
  lastRadioState = null;
  outageStartedMs = null;
  notifiedOutageMs = null;
  seenContacts.clear();
}

// ---- Node filter ----------------------------------------------------------------------------

/** The Nodes facet thread an event belongs to (`us`, `contact:<key>`, `channel:<key>`). */
export function eventThreadId(event: FeedEvent): string {
  switch (event.id) {
    case 'radio.offline':
    case 'radio.online':
      return 'us';
    case 'channel.found':
      return `channel:${event.channelKey}`;
    default:
      return `contact:${event.publicKey}`;
  }
}

/** The events the Events source and the picked node let through (a node narrows them to its thread). */
export function visibleFeedEvents(
  events: readonly FeedEvent[],
  enabled: boolean,
  node: string | null
): readonly FeedEvent[] {
  if (!enabled) return [];
  if (!node) return events;
  const wanted = node.toLowerCase();
  return events.filter((event) => eventThreadId(event).toLowerCase() === wanted);
}
