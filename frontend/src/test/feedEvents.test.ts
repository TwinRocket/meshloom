import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { activateFeedStore, getFeedSnapshot, resetFeedStore } from '../stores/feedStore';
import { recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';
import type { Channel, Contact, HealthStatus, RawPacket } from '../types';
import {
  FEED_ONLY_EVENT_IDS,
  NOTIFICATION_EVENT_IDS,
  RADIO_OFFLINE_GRACE_MS,
  eventThreadId,
  forgetContact,
  isFeedOnlyEvent,
  noteChannelFrame,
  noteContactFrame,
  noteRadioHealth,
  visibleFeedEvents,
} from '../utils/feedEvents';

const KEY = 'ab'.repeat(32);
const NOW = 1_700_000_000_000;

function health(radio_state: NonNullable<HealthStatus['radio_state']>): HealthStatus {
  return {
    status: 'ok',
    radio_connected: radio_state === 'connected',
    radio_initializing: radio_state === 'initializing',
    radio_state,
  } as HealthStatus;
}

function contact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: KEY,
    name: 'Tower',
    type: 2,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: NOW / 1000 - 5,
    ...overrides,
  };
}

const events = () => getFeedSnapshot().events;
const flush = () => Promise.resolve();

describe('feed events: radio', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    resetRawPacketStore();
    resetFeedStore();
    activateFeedStore();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('records radio.offline once the grace delay has passed, stamped at the outage start', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS - 1);
    await flush();
    expect(events()).toHaveLength(0);
    vi.advanceTimersByTime(1);
    await flush();
    expect(events().map((e) => e.id)).toEqual(['radio.offline']);
    expect(events()[0].at).toBe(NOW / 1000);
  });

  it('records radio.online with the downtime after a reported outage, and only then', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(90_000);
    noteRadioHealth(health('connected'));
    await flush();
    expect(events().map((e) => e.id)).toEqual(['radio.offline', 'radio.online']);
    const online = events()[1];
    expect(online.id === 'radio.online' && online.downtimeS).toBe(90);
    // A later, unrelated `connected` frame adds nothing.
    noteRadioHealth(health('connected'));
    await flush();
    expect(events()).toHaveLength(2);
  });

  it('produces nothing for a flap shorter than the grace delay', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(30_000);
    noteRadioHealth(health('connected'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS * 2);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('counts connecting after disconnected as the same outage, not a restart of the delay', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(40_000);
    noteRadioHealth(health('connecting'));
    vi.advanceTimersByTime(20_000);
    await flush();
    expect(events().map((e) => e.id)).toEqual(['radio.offline']);
  });

  it('ignores an expected reboot: rebooting then connected produces nothing', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('rebooting'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS * 3);
    noteRadioHealth(health('initializing'));
    noteRadioHealth(health('connected'));
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('drops a pending outage when the radio is paused or rebooted meanwhile', async () => {
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(10_000);
    noteRadioHealth(health('rebooting'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS * 2);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('treats the first frame as a baseline, not a transition', async () => {
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS * 2);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('records nothing while the feed was never opened, and no orphan online afterwards', async () => {
    resetFeedStore();
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS * 2);
    activateFeedStore();
    noteRadioHealth(health('connected'));
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('stamps rows on the server clock using the measured skew', async () => {
    // The browser is 100 s ahead of the server.
    const packet = {
      id: 1,
      observation_id: 1,
      timestamp: NOW / 1000 - 100,
      data: '0100bbaa',
      payload_type: 'Request',
      snr: null,
      rssi: null,
      decrypted: false,
      decrypted_info: null,
    } as RawPacket;
    recordRawPacket(packet);
    noteRadioHealth(health('connected'));
    noteRadioHealth(health('disconnected'));
    vi.advanceTimersByTime(RADIO_OFFLINE_GRACE_MS);
    await flush();
    expect(events()[0].at).toBeCloseTo(NOW / 1000 - 100, 0);
    expect(events()[0].atMs).toBe(NOW);
  });
});

describe('feed events: contacts and channels', () => {
  beforeEach(() => {
    resetRawPacketStore();
    resetFeedStore();
    activateFeedStore();
  });

  it('records contact.first_seen for a key the client never held, stamped with first_seen', async () => {
    noteContactFrame(contact(), { previous: undefined, resyncing: false }, NOW);
    await flush();
    expect(events()).toHaveLength(1);
    expect(events()[0]).toMatchObject({
      id: 'contact.first_seen',
      publicKey: KEY,
      name: 'Tower',
      at: NOW / 1000 - 5,
    });
  });

  it('produces no row for a known, unchanged contact (last_seen snapshots)', async () => {
    const known = contact();
    noteContactFrame({ ...known, last_seen: 1 }, { previous: known, resyncing: false }, NOW);
    noteContactFrame({ ...known, last_seen: 2 }, { previous: known, resyncing: false }, NOW);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('dedupes a burst of frames for one new contact', async () => {
    for (let i = 0; i < 5; i++) {
      noteContactFrame(contact({ last_seen: i }), { previous: undefined, resyncing: false }, NOW);
    }
    await flush();
    expect(events()).toHaveLength(1);
  });

  it('produces no row while a resync is in flight, and the contact is not new afterwards', async () => {
    noteContactFrame(contact(), { previous: undefined, resyncing: true }, NOW);
    noteContactFrame(contact(), { previous: undefined, resyncing: false }, NOW);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('records contact.renamed (feed only) when a known contact changes its name', async () => {
    const known = contact();
    noteContactFrame(contact({ name: 'Tower 2' }), { previous: known, resyncing: false }, NOW);
    noteContactFrame(contact({ name: 'Tower 2' }), { previous: known, resyncing: false }, NOW);
    await flush();
    expect(events()).toHaveLength(1);
    const renamed = events()[0];
    expect(renamed).toMatchObject({ id: 'contact.renamed', from: 'Tower', to: 'Tower 2' });
    expect(isFeedOnlyEvent(renamed)).toBe(true);
  });

  it('does not rename to or from an empty name', async () => {
    const known = contact();
    noteContactFrame(contact({ name: null }), { previous: known, resyncing: false }, NOW);
    await flush();
    expect(events()).toHaveLength(0);
  });

  it('reports a deleted contact that comes back', async () => {
    noteContactFrame(contact(), { previous: undefined, resyncing: false }, NOW);
    forgetContact(KEY);
    noteContactFrame(
      contact({ first_seen: NOW / 1000 + 1 }),
      { previous: undefined, resyncing: false },
      NOW
    );
    await flush();
    expect(events().map((e) => e.id)).toEqual(['contact.first_seen', 'contact.first_seen']);
  });

  it('records channel.found only for a pending channel the client did not hold', async () => {
    const channel = {
      key: 'AA'.repeat(16),
      name: '#found',
      membership: 'pending',
    } as Channel;
    noteChannelFrame(channel, true, NOW);
    noteChannelFrame({ ...channel, membership: 'adopted' }, false, NOW);
    noteChannelFrame(channel, false, NOW);
    noteChannelFrame(channel, false, NOW);
    await flush();
    expect(events()).toHaveLength(1);
    expect(events()[0]).toMatchObject({ id: 'channel.found', name: '#found' });
  });

  it('orders rows by their server time', async () => {
    noteContactFrame(
      contact({ public_key: 'cd'.repeat(32), first_seen: 2000 }),
      { previous: undefined, resyncing: false },
      NOW
    );
    noteContactFrame(
      contact({ public_key: 'ef'.repeat(32), first_seen: 1000 }),
      { previous: undefined, resyncing: false },
      NOW
    );
    await flush();
    expect(events().map((e) => e.at)).toEqual([1000, 2000]);
  });
});

describe('feed events: node filter and ids', () => {
  beforeEach(() => {
    resetFeedStore();
    activateFeedStore();
  });

  it('narrows events to the picked thread, and hides them when the source is off', async () => {
    noteContactFrame(contact(), { previous: undefined, resyncing: false }, NOW);
    noteChannelFrame({ key: 'AA'.repeat(16), name: 'x', membership: 'pending' } as Channel, false);
    await flush();
    const all = events();
    expect(visibleFeedEvents(all, true, null)).toHaveLength(2);
    expect(visibleFeedEvents(all, false, null)).toHaveLength(0);
    const picked = visibleFeedEvents(all, true, `contact:${KEY.toUpperCase()}`);
    expect(picked.map((e) => eventThreadId(e))).toEqual([`contact:${KEY}`]);
    expect(visibleFeedEvents(all, true, 'us')).toHaveLength(0);
  });

  it('keeps notification ids apart from feed-only ones', () => {
    for (const id of FEED_ONLY_EVENT_IDS) expect(NOTIFICATION_EVENT_IDS).not.toContain(id);
  });
});
