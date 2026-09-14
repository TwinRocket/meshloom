import { describe, expect, it } from 'vitest';

import { isDispatchableWsEvent, parseWsEvent } from '../wsEvents';

describe('wsEvents', () => {
  it('parses contact_deleted events', () => {
    const event = parseWsEvent(
      JSON.stringify({ type: 'contact_deleted', data: { public_key: 'aa' } })
    );

    expect(event).toEqual({
      type: 'contact_deleted',
      data: { public_key: 'aa' },
    });
  });

  it('parses contact_resolved events', () => {
    const event = parseWsEvent(
      JSON.stringify({
        type: 'contact_resolved',
        data: {
          previous_public_key: 'abc123def456',
          contact: {
            public_key: 'aa'.repeat(32),
            name: null,
            type: 0,
            flags: 0,
            direct_path: null,
            direct_path_len: -1,
            direct_path_hash_mode: -1,
            last_advert: null,
            lat: null,
            lon: null,
            last_seen: null,
            on_radio: false,
            last_contacted: null,
            last_read_at: null,
            first_seen: null,
          },
        },
      })
    );

    expect(event).toEqual({
      type: 'contact_resolved',
      data: {
        previous_public_key: 'abc123def456',
        contact: {
          public_key: 'aa'.repeat(32),
          name: null,
          type: 0,
          flags: 0,
          direct_path: null,
          direct_path_len: -1,
          direct_path_hash_mode: -1,
          last_advert: null,
          lat: null,
          lon: null,
          last_seen: null,
          on_radio: false,
          last_contacted: null,
          last_read_at: null,
          first_seen: null,
        },
      },
    });
  });

  it('parses message_deleted events', () => {
    const event = parseWsEvent(
      JSON.stringify({ type: 'message_deleted', data: { message_id: 42 } })
    );

    expect(event).toEqual({
      type: 'message_deleted',
      data: { message_id: 42 },
    });
  });

  it('keeps identity fields on health events instead of a new event type', () => {
    const event = parseWsEvent(
      JSON.stringify({
        type: 'health',
        data: {
          status: 'degraded',
          radio_connected: false,
          radio_state: 'identity_mismatch',
          transport_configured: true,
          identity: {
            previous_public_key: 'aa',
            new_public_key: 'bb',
            mesh_contacts: 1,
            mesh_messages: 2,
            last_activity: null,
          },
        },
      })
    );

    expect(event.type).toBe('health');
    expect(event).toMatchObject({
      type: 'health',
      data: {
        radio_state: 'identity_mismatch',
        transport_configured: true,
        identity: {
          previous_public_key: 'aa',
          new_public_key: 'bb',
        },
      },
    });
  });

  it('parses channel_deleted events', () => {
    const event = parseWsEvent(JSON.stringify({ type: 'channel_deleted', data: { key: 'bb' } }));

    expect(event).toEqual({
      type: 'channel_deleted',
      data: { key: 'bb' },
    });
  });

  it('parses community_packet events even when Stats is not emitting yet', () => {
    const data = {
      v: 2,
      event_id: 'e1',
      hash8: 'deadbeef',
      type: 'advert',
      path: ['ab12'],
      hop_count: 1,
      hops: [{ token: 'ab12', lat: 45.7, lon: 4.8, confidence: 'exact' }],
      ear: { lat: 45.72, lon: 5.08, source: 'advert' },
      iata: 'LYS',
      t: 1710000000000,
      ear_id: 'ear-1',
    };
    const event = parseWsEvent(JSON.stringify({ type: 'community_packet', data }));

    expect(event).toEqual({ type: 'community_packet', data });
    expect(isDispatchableWsEvent(event)).toBe(true);
  });

  it('parses community_live close codes for Relancer banners', () => {
    const live = parseWsEvent(
      JSON.stringify({
        type: 'community_live',
        data: { close_code: 4001, opted_out: false, connected: false },
      })
    );
    expect(live).toEqual({
      type: 'community_live',
      data: { close_code: 4001, opted_out: false, connected: false },
    });
  });

  it('returns unknown events with rawType preserved', () => {
    const event = parseWsEvent(JSON.stringify({ type: 'mystery', data: { ok: true } }));

    expect(event).toEqual({
      type: 'unknown',
      rawType: 'mystery',
      data: { ok: true },
    });
  });

  it('rejects invalid envelopes', () => {
    expect(() => parseWsEvent(JSON.stringify({ data: {} }))).toThrow(
      'Invalid WebSocket event envelope'
    );
  });

  it('accepts extra fields on known events', () => {
    const event = parseWsEvent(
      JSON.stringify({
        type: 'contact_deleted',
        data: { public_key: 'aa', extra: true },
      })
    );
    expect(isDispatchableWsEvent(event)).toBe(true);
  });

  it('rejects known events missing immediately-dereferenced fields', () => {
    expect(
      isDispatchableWsEvent(parseWsEvent(JSON.stringify({ type: 'message', data: null })))
    ).toBe(false);
    expect(
      isDispatchableWsEvent(parseWsEvent(JSON.stringify({ type: 'contact', data: { name: 'x' } })))
    ).toBe(false);
    expect(
      isDispatchableWsEvent(parseWsEvent(JSON.stringify({ type: 'contact_deleted', data: {} })))
    ).toBe(false);
    expect(
      isDispatchableWsEvent(parseWsEvent(JSON.stringify({ type: 'channel_deleted', data: {} })))
    ).toBe(false);
    expect(
      isDispatchableWsEvent(
        parseWsEvent(JSON.stringify({ type: 'message_acked', data: { message_id: 1 } }))
      )
    ).toBe(false);
    expect(isDispatchableWsEvent(parseWsEvent(JSON.stringify({ type: 'error', data: {} })))).toBe(
      false
    );
    expect(
      isDispatchableWsEvent(
        parseWsEvent(JSON.stringify({ type: 'success', data: { details: 'x' } }))
      )
    ).toBe(false);
  });
});
