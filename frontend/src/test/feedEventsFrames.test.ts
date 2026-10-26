/**
 * MSHL-166: the Feed's event rows from the `notification_event` frame. The frame and the
 * client reducers share one memory, so the rows and their sentences are the same whichever
 * source reports first, and with no frame at all (`rules.emit_ws_events` off, or an event no
 * enabled channel routes). The frames are the documented fixtures the backend checks
 * (`tests/test_websocket.py`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { describeFeedEvent } from '../components/feed/feedEventText';
import i18n from '../i18n';
import { activateFeedStore, getFeedSnapshot, resetFeedStore } from '../stores/feedStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import type { Channel, Contact, HealthStatus } from '../types';
import {
  NOTIFICATION_EVENT_IDS,
  noteChannelFrame,
  noteContactFrame,
  noteNotificationEvent,
  noteRadioHealth,
  setRadioOfflineGraceS,
  type FeedEvent,
} from '../utils/feedEvents';
import type { NotificationEventFrame } from '../wsEvents';
import fixtures from './fixtures/websocket_events.json';

const NOW = 1_700_000_000_000;
const KEY = 'ab'.repeat(32);
const CHANNEL_KEY = 'AA'.repeat(16);

function fixtureFrame(
  name:
    | 'notification_event_radio_offline'
    | 'notification_event_radio_online'
    | 'notification_event_contact_first_seen'
    | 'notification_event_channel_found'
    | 'notification_event_mention',
  overrides: Partial<NotificationEventFrame> = {}
): NotificationEventFrame {
  const { type, data } = fixtures[name].expected_ws_event;
  expect(type).toBe('notification_event');
  return { ...(data as NotificationEventFrame), ...overrides };
}

function health(radio_state: NonNullable<HealthStatus['radio_state']>): HealthStatus {
  return {
    status: 'ok',
    radio_connected: radio_state === 'connected',
    radio_initializing: radio_state === 'initializing',
    radio_state,
  } as HealthStatus;
}

const TOWER = {
  public_key: KEY,
  name: 'Tower',
  type: 2,
  first_seen: NOW / 1000 - 5,
} as Contact;
const FOUND = { key: CHANNEL_KEY, name: '#found', membership: 'pending' } as Channel;

const flush = () => Promise.resolve();
const rows = () => getFeedSnapshot().events;

function fresh(): void {
  vi.setSystemTime(NOW);
  resetRawPacketStore();
  resetFeedStore();
  activateFeedStore();
}

/** Rows plus their sentence in both UI languages, as the Feed renders them. */
async function rendered(): Promise<Array<FeedEvent & { en: string; fr: string }>> {
  await flush();
  const contacts = new Map<string, Contact>();
  const out = [];
  for (const event of rows()) {
    const say = async (lng: string) => {
      await i18n.changeLanguage(lng);
      return describeFeedEvent(event, i18n.t, lng, contacts, []).sentence;
    };
    out.push({ ...event, en: await say('en'), fr: await say('fr') });
  }
  return out;
}

type Scenario = (frames: boolean) => void;

/** The rows of a scenario without frames (reducers only), then with them. */
async function both(scenario: Scenario) {
  fresh();
  scenario(false);
  const reducersOnly = await rendered();
  fresh();
  scenario(true);
  const withFrames = await rendered();
  return { reducersOnly, withFrames };
}

const radioOutage =
  (frameFirst: boolean): Scenario =>
  (frames) => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    // The server's grace equals the session's default here: the frame lands with the timer.
    vi.advanceTimersByTime(59_999);
    if (frames) noteNotificationEvent(fixtureFrame('notification_event_radio_offline'));
    vi.advanceTimersByTime(30_001);
    const online = fixtureFrame('notification_event_radio_online', {
      occurred_at: (NOW + 90_000) / 1000,
    });
    if (frames && frameFirst) noteNotificationEvent(online);
    noteRadioHealth(health('connected'));
    if (frames && !frameFirst) noteNotificationEvent(online);
  };

const firstSeen =
  (frameFirst: boolean): Scenario =>
  (frames) => {
    const frame = fixtureFrame('notification_event_contact_first_seen');
    if (frames && frameFirst) noteNotificationEvent(frame, NOW);
    noteContactFrame(TOWER, { previous: undefined, resyncing: false }, NOW);
    if (frames && !frameFirst) noteNotificationEvent(frame, NOW);
    // A later snapshot of the same contact adds nothing either way.
    noteContactFrame({ ...TOWER, last_seen: 1 }, { previous: TOWER, resyncing: false }, NOW);
  };

const channelFound =
  (frameFirst: boolean): Scenario =>
  (frames) => {
    const frame = fixtureFrame('notification_event_channel_found');
    if (frames && frameFirst) noteNotificationEvent(frame, NOW);
    noteChannelFrame(FOUND, false, NOW);
    if (frames && !frameFirst) noteNotificationEvent(frame, NOW);
  };

describe('feed events from notification_event frames', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fresh();
  });
  afterEach(async () => {
    vi.useRealTimers();
    setRadioOfflineGraceS(null);
    await i18n.changeLanguage('fr');
  });

  it.each([
    ['radio outage, frame first', radioOutage(true), ['radio.offline', 'radio.online']],
    ['radio outage, health first', radioOutage(false), ['radio.offline', 'radio.online']],
    ['first sighting, frame first', firstSeen(true), ['contact.first_seen']],
    ['first sighting, contact first', firstSeen(false), ['contact.first_seen']],
    ['channel found, frame first', channelFound(true), ['channel.found']],
    ['channel found, channel first', channelFound(false), ['channel.found']],
  ])('gives the same rows and sentences as the reducers: %s', async (_name, scenario, ids) => {
    const { reducersOnly, withFrames } = await both(scenario);
    expect(reducersOnly.map((row) => row.id)).toEqual(ids);
    expect(withFrames).toEqual(reducersOnly);
    for (const row of withFrames) {
      expect(row.en).not.toBe('');
      expect(row.fr).not.toBe('');
    }
  });

  it('keeps the rows without any frame (emit_ws_events off, or an unrouted event)', async () => {
    for (const scenario of [radioOutage(true), firstSeen(true), channelFound(true)]) {
      fresh();
      scenario(false);
      await flush();
      expect(rows().length).toBeGreaterThan(0);
    }
  });

  it("reports an outage at the server's grace, sooner than the session default", async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(10_000);
    noteNotificationEvent(fixtureFrame('notification_event_radio_offline'));
    await flush();
    expect(rows()).toMatchObject([{ id: 'radio.offline', key: `radio.offline:${NOW}` }]);
    // The session timer that follows adds nothing.
    vi.advanceTimersByTime(60_000);
    await flush();
    expect(rows()).toHaveLength(1);
  });

  it('reports no outage it did not see start, and no orphan online', async () => {
    noteNotificationEvent(fixtureFrame('notification_event_radio_offline'));
    noteNotificationEvent(fixtureFrame('notification_event_radio_online'));
    noteRadioHealth(health('disconnected'));
    noteNotificationEvent(fixtureFrame('notification_event_radio_online'));
    await flush();
    expect(rows()).toHaveLength(0);
  });

  it('dedupes a frame replayed after a reconnect', async () => {
    const frame = fixtureFrame('notification_event_contact_first_seen');
    noteNotificationEvent(frame, NOW);
    noteNotificationEvent(frame, NOW + 1000);
    noteNotificationEvent(fixtureFrame('notification_event_channel_found'), NOW);
    noteNotificationEvent(fixtureFrame('notification_event_channel_found'), NOW + 1000);
    await flush();
    expect(rows().map((row) => row.id)).toEqual(['contact.first_seen', 'channel.found']);
  });

  it('skips the contact types the catalogue never announces', async () => {
    for (const contact_type of [0, 3]) {
      const frame = fixtureFrame('notification_event_contact_first_seen');
      noteNotificationEvent({ ...frame, data: { ...frame.data, contact_type } }, NOW);
    }
    await flush();
    expect(rows()).toHaveLength(0);
  });

  it('records nothing while the Feed was never opened', async () => {
    resetFeedStore();
    noteNotificationEvent(fixtureFrame('notification_event_channel_found'), NOW);
    activateFeedStore();
    await flush();
    expect(rows()).toHaveLength(0);
  });

  it('never makes a row from a conversation, whatever the role (kiosk included)', async () => {
    // What a role-limited session could at most get (public channel mention), and what the
    // server keeps from it (DM, PRIV, private-key mention), plus admin-only and other ids.
    const mention = fixtureFrame('notification_event_mention');
    const priv = { conversation_key: KEY, conversation_type: 'PRIV' as const };
    const frames: NotificationEventFrame[] = [
      mention,
      { ...mention, event: 'message.channel' },
      { ...mention, conversation_open: false },
      { ...mention, event: 'message.dm', ...priv },
      { ...mention, event: 'message.keyword', ...priv },
      { ...fixtureFrame('notification_event_contact_first_seen'), ...priv },
      { ...fixtureFrame('notification_event_channel_found'), ...priv },
      { ...mention, event: 'notify.channel_failed' },
      { ...mention, event: 'telemetry.alert' },
      { ...mention, event: 'not.in.the.catalogue' },
    ];
    for (const frame of frames) noteNotificationEvent(frame, NOW);
    await flush();
    expect(rows()).toHaveLength(0);
  });

  it('reads only the catalogue ids the Feed declares', () => {
    expect([...NOTIFICATION_EVENT_IDS].sort()).toEqual(
      ['channel.found', 'contact.first_seen', 'radio.offline', 'radio.online'].sort()
    );
  });
});
