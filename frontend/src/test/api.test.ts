/**
 * Tests for API utilities.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiError, formatApiError, getApiErrorCode, isAbortError, api } from '../api';
import i18n from '../i18n';

describe('formatApiError', () => {
  it('returns the backend message when no code is present', () => {
    expect(formatApiError(new ApiError('Radio not connected', 423), i18n.t)).toBe(
      'Radio not connected'
    );
  });

  it('translates a known error code', () => {
    const err = new ApiError('Radio not connected', 423, { code: 'radio_not_connected' });
    i18n.addResource('en', 'translation', 'errors.radio_not_connected', 'Radio is offline');
    i18n.addResource('fr', 'translation', 'errors.radio_not_connected', 'Radio hors ligne');
    expect(getApiErrorCode(err.detail)).toEqual({ code: 'radio_not_connected', params: {} });
    expect(formatApiError(err, i18n.t)).toBe(i18n.t('errors.radio_not_connected'));
  });

  it('falls back to Error.message for unknown values', () => {
    expect(formatApiError(new Error('boom'), i18n.t)).toBe('boom');
    expect(formatApiError('plain', i18n.t)).toBe('plain');
  });
});

describe('isAbortError', () => {
  it('returns true for AbortError', () => {
    const controller = new AbortController();
    controller.abort();

    // Create an error that mimics fetch abort
    const error = new DOMException('The operation was aborted', 'AbortError');

    expect(isAbortError(error)).toBe(true);
  });

  it('returns true for Error with name AbortError', () => {
    const error = new Error('Request cancelled');
    error.name = 'AbortError';

    expect(isAbortError(error)).toBe(true);
  });

  it('returns false for regular Error', () => {
    const error = new Error('Something went wrong');

    expect(isAbortError(error)).toBe(false);
  });

  it('returns false for TypeError', () => {
    const error = new TypeError('Network failure');

    expect(isAbortError(error)).toBe(false);
  });

  it('returns false for null', () => {
    expect(isAbortError(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(isAbortError(undefined)).toBe(false);
  });

  it('returns false for non-Error objects', () => {
    expect(isAbortError({ message: 'error' })).toBe(false);
    expect(isAbortError('error string')).toBe(false);
    expect(isAbortError(42)).toBe(false);
  });

  it('returns false for Error subclasses with different names', () => {
    class CustomError extends Error {
      constructor() {
        super('Custom error');
        this.name = 'CustomError';
      }
    }

    expect(isAbortError(new CustomError())).toBe(false);
  });
});

describe('fetchJson (via api methods)', () => {
  const mockFetch = vi.fn();

  // Replace global fetch before each test, restore after
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function installMockFetch() {
    mockFetch.mockReset();
    global.fetch = mockFetch;
  }

  describe('successful responses', () => {
    it('returns parsed JSON on a successful response', async () => {
      installMockFetch();
      const healthData = {
        status: 'connected',
        radio_connected: true,
        radio_initializing: false,
        connection_info: 'Serial: /dev/ttyUSB0',
        database_size_mb: 1.2,
        oldest_undecrypted_timestamp: null,
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(healthData),
      });

      const result = await api.getHealth();

      expect(result).toEqual(healthData);
    });

    it('GETs /updates', async () => {
      installMockFetch();
      const updates = {
        current: '1.0.0',
        latest: '1.1.0',
        update_available: true,
        html_url: 'https://github.com/TwinRocket/meshloom/releases/tag/v1.1.0',
        install_kind: 'package',
        apply_supported: true,
        auto_update: false,
        job: {
          state: 'idle',
          phase: null,
          percent: null,
          error: null,
          started_at: null,
        },
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(updates),
      });

      await expect(api.getUpdates()).resolves.toEqual(updates);
      expect(mockFetch.mock.calls[0][0]).toBe('./api/updates');
    });

    it('POSTs /updates/apply', async () => {
      installMockFetch();
      const updates = {
        current: '1.0.0',
        latest: '1.1.0',
        update_available: true,
        html_url: null,
        install_kind: 'package',
        apply_supported: true,
        auto_update: false,
        job: {
          state: 'applying',
          phase: 'preparing',
          percent: 10,
          error: null,
          started_at: 1,
        },
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(updates),
      });

      await expect(api.applyUpdate()).resolves.toEqual(updates);
      expect(mockFetch.mock.calls[0][0]).toBe('./api/updates/apply');
      expect(mockFetch.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    });

    it('PATCHes /updates/settings', async () => {
      installMockFetch();
      const updates = {
        current: '1.0.0',
        latest: '1.1.0',
        update_available: true,
        html_url: null,
        install_kind: 'package',
        apply_supported: true,
        auto_update: true,
        job: {
          state: 'idle',
          phase: null,
          percent: null,
          error: null,
          started_at: null,
        },
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(updates),
      });

      await expect(api.patchUpdateSettings({ auto_update: true })).resolves.toEqual(updates);
      expect(mockFetch.mock.calls[0][0]).toBe('./api/updates/settings');
      expect(mockFetch.mock.calls[0][1]).toMatchObject({
        method: 'PATCH',
        body: JSON.stringify({ auto_update: true }),
      });
    });

    it('calls fetch with /api prefix', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      await api.getContacts();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [url] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/contacts?limit=100&offset=0');
    });

    it('builds repeater advert path endpoint query', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      await api.getRepeaterAdvertPaths(12);

      const [url] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/contacts/repeaters/advert-paths?limit_per_repeater=12');
    });
  });

  describe('error handling', () => {
    it('extracts detail from FastAPI JSON error response', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        text: () => Promise.resolve('{"detail": "Radio not connected"}'),
      });

      await expect(api.getHealth()).rejects.toThrow('Radio not connected');
    });

    it('extracts message from object detail with a stable code', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 423,
        statusText: 'Locked',
        text: () =>
          Promise.resolve(
            JSON.stringify({
              detail: { code: 'radio_not_connected', message: 'Radio not connected' },
            })
          ),
      });

      await expect(api.getHealth()).rejects.toMatchObject({
        message: 'Radio not connected',
        status: 423,
        detail: { code: 'radio_not_connected', message: 'Radio not connected' },
      });
    });

    it('keeps reason-based object details for telemetry-style errors', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 409,
        statusText: 'Conflict',
        text: () =>
          Promise.resolve(JSON.stringify({ detail: { reason: 'ambiguous', candidates: [] } })),
      });

      await expect(api.getHealth()).rejects.toMatchObject({
        message: 'ambiguous',
        status: 409,
        detail: { reason: 'ambiguous', candidates: [] },
      });
    });

    it('uses raw text when error response is not JSON', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        text: () => Promise.resolve('Something broke on the server'),
      });

      await expect(api.getHealth()).rejects.toThrow('Something broke on the server');
    });

    it('uses statusText when error text is empty', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 502,
        statusText: 'Bad Gateway',
        text: () => Promise.resolve(''),
      });

      await expect(api.getHealth()).rejects.toThrow('Bad Gateway');
    });

    it('uses raw text when JSON lacks detail field', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: 'Unprocessable Entity',
        text: () => Promise.resolve('{"error": "validation failed"}'),
      });

      await expect(api.getHealth()).rejects.toThrow('{"error": "validation failed"}');
    });
  });

  describe('Content-Type header', () => {
    it('omits Content-Type on GET requests (no body)', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'connected' }),
      });

      await api.getHealth();

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).not.toHaveProperty('Content-Type');
    });

    it('sends Content-Type: application/json on POST requests with body', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ id: 1, text: 'hello' }),
      });

      await api.sendDirectMessage('abc123', 'hello');

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).toEqual(
        expect.objectContaining({ 'Content-Type': 'application/json' })
      );
    });

    it('omits Content-Type on POST requests without a body', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            contact: null,
            forward_path: { path: '', path_len: 0, path_hash_mode: 0 },
            return_path: { path: '', path_len: 0, path_hash_mode: 0 },
          }),
      });

      await api.requestPathDiscovery('aa'.repeat(32));

      const [, options] = mockFetch.mock.calls[0];
      expect(options.method).toBe('POST');
      expect(options.headers).not.toHaveProperty('Content-Type');
    });
  });

  describe('HTTP methods and body', () => {
    it('sends POST with JSON body for sendDirectMessage', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: 1,
            type: 'PRIV',
            text: 'hello',
            destination: 'abc123',
          }),
      });

      await api.sendDirectMessage('abc123', 'hello');

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/messages/direct');
      expect(options.method).toBe('POST');
      expect(JSON.parse(options.body)).toEqual({
        destination: 'abc123',
        text: 'hello',
      });
    });

    it('sends PATCH with JSON body for updateRadioConfig', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ name: 'NewName' }),
      });

      await api.updateRadioConfig({ name: 'NewName' });

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/radio/config');
      expect(options.method).toBe('PATCH');
      expect(JSON.parse(options.body)).toEqual({ name: 'NewName' });
    });

    it('calls radio transport and identity endpoints', async () => {
      installMockFetch();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ status: 'ok' }),
      });

      await api.getRadioTransport();
      await api.updateRadioTransport({ transport: 'tcp', tcp_host: '10.0.0.1', tcp_port: 5000 });
      await api.getRadioProxy();
      await api.updateRadioProxy({ enabled: true, port: 5001 });
      await api.scanRadioBle();
      await api.adoptRadioIdentity({ confirm_wipe: false });
      await api.rejectRadioIdentity();

      expect(mockFetch.mock.calls.map(([url, options]) => [url, options.method])).toEqual([
        ['./api/radio/transport', undefined],
        ['./api/radio/transport', 'PUT'],
        ['./api/radio/proxy', undefined],
        ['./api/radio/proxy', 'PATCH'],
        ['./api/radio/transport/ble-scan', 'POST'],
        ['./api/radio/identity/adopt', 'POST'],
        ['./api/radio/identity/reject', 'POST'],
      ]);
      expect(JSON.parse(mockFetch.mock.calls[1][1].body)).toEqual({
        transport: 'tcp',
        tcp_host: '10.0.0.1',
        tcp_port: 5000,
      });
      expect(JSON.parse(mockFetch.mock.calls[3][1].body)).toEqual({
        enabled: true,
        port: 5001,
      });
      expect(JSON.parse(mockFetch.mock.calls[5][1].body)).toEqual({ confirm_wipe: false });
    });

    it('sends PUT with JSON body for setPrivateKey', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok' }),
      });

      await api.setPrivateKey('my-secret-key');

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/radio/private-key');
      expect(options.method).toBe('PUT');
      expect(JSON.parse(options.body)).toEqual({ private_key: 'my-secret-key' });
    });

    it('sends POST with JSON body for mesh discovery', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ target: 'repeaters', duration_seconds: 8, results: [] }),
      });

      await api.discoverMesh('repeaters');

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/radio/discover');
      expect(options.method).toBe('POST');
      expect(JSON.parse(options.body)).toEqual({ target: 'repeaters' });
    });

    it('sends DELETE for deleteContact', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok' }),
      });

      await api.deleteContact('pubkey123');

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/contacts/pubkey123');
      expect(options.method).toBe('DELETE');
    });

    it('sends POST with flood mode for sendAdvertisement', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok' }),
      });

      await api.sendAdvertisement();

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/radio/advertise');
      expect(options.method).toBe('POST');
      expect(options.body).toBe(JSON.stringify({ mode: 'flood' }));
    });

    it('sends POST with zero-hop mode for sendAdvertisement', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok' }),
      });

      await api.sendAdvertisement('zero_hop');

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/radio/advertise');
      expect(options.method).toBe('POST');
      expect(options.body).toBe(JSON.stringify({ mode: 'zero_hop' }));
    });
  });

  describe('AbortSignal passthrough', () => {
    it('passes signal option through to fetch for getMessages', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      const controller = new AbortController();
      await api.getMessages({ limit: 10 }, controller.signal);

      const [, options] = mockFetch.mock.calls[0];
      expect(options.signal).toBe(controller.signal);
    });

    it('calls fetch without signal when none is provided', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      await api.getMessages({ limit: 10 });

      const [, options] = mockFetch.mock.calls[0];
      expect(options.signal).toBeUndefined();
    });
  });

  describe('api.getMessages query parameter construction', () => {
    it('builds query string with all parameters', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      await api.getMessages({
        limit: 50,
        offset: 10,
        type: 'PRIV',
        conversation_key: 'abc123',
        before: 1700000000,
        before_id: 99,
      });

      const [url] = mockFetch.mock.calls[0];
      expect(url).toContain('./api/messages?');
      expect(url).toContain('limit=50');
      expect(url).toContain('offset=10');
      expect(url).toContain('type=PRIV');
      expect(url).toContain('conversation_key=abc123');
      expect(url).toContain('before=1700000000');
      expect(url).toContain('before_id=99');
    });

    it('builds URL without query string when no params given', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });

      await api.getMessages();

      const [url] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/messages');
    });
  });

  describe('Meshloom Stats community client', () => {
    const communityStatus = {
      enabled: false,
      locked: false,
      iata: '',
      broker_host: 'mqtt.meshloom.app',
      api_base: 'https://api.meshloom.app',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    };

    it('GETs /community', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(communityStatus),
      });

      await expect(api.getCommunity()).resolves.toEqual(communityStatus);
      expect(mockFetch.mock.calls[0][0]).toBe('./api/community');
    });

    it('PATCHes /community', async () => {
      installMockFetch();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ ...communityStatus, enabled: true, iata: 'CDG' }),
      });

      await api.updateCommunity({ enabled: true, iata: 'CDG' });

      const [url, options] = mockFetch.mock.calls[0];
      expect(url).toBe('./api/community');
      expect(options.method).toBe('PATCH');
      expect(options.body).toBe(JSON.stringify({ enabled: true, iata: 'CDG' }));
    });

    it('GETs me stats, public stats, and IATA bind/override', async () => {
      installMockFetch();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      await api.getCommunityMeStats();
      await api.getCommunityStats();
      await api.bindCommunityIata({ iata: 'CDG' });
      await api.overrideCommunityIata();

      expect(mockFetch.mock.calls[0][0]).toBe('./api/community/me/stats');
      expect(mockFetch.mock.calls[1][0]).toBe('./api/community/stats');
      expect(mockFetch.mock.calls[2][0]).toBe('./api/community/me/iata');
      expect(mockFetch.mock.calls[2][1].method).toBe('PUT');
      expect(mockFetch.mock.calls[2][1].body).toBe(JSON.stringify({ iata: 'CDG' }));
      expect(mockFetch.mock.calls[3][0]).toBe('./api/community/me/iata/override');
      expect(mockFetch.mock.calls[3][1].method).toBe('POST');
    });

    it('GETs IATA hashtags and PUTs discovered names', async () => {
      installMockFetch();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ hashtags: [{ name: 'mesh', hash_byte: 'ab' }] }),
      });

      await api.getCommunityHashtags('lys');
      await api.putCommunityHashtags(['mesh']);

      expect(mockFetch.mock.calls[0][0]).toBe('./api/community/iata/lys/hashtags');
      expect(mockFetch.mock.calls[1][0]).toBe('./api/community/me/hashtags');
      expect(mockFetch.mock.calls[1][1].method).toBe('PUT');
      expect(mockFetch.mock.calls[1][1].body).toBe(JSON.stringify({ names: ['mesh'] }));
    });

    it('subscribes, unsubscribes, and Relancer the Stats live relay', async () => {
      installMockFetch();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            session_id: 's1',
            close_code: null,
            opted_out: false,
            connected: true,
          }),
      });

      await api.subscribeCommunityLive();
      await api.subscribeCommunityLive('s1');
      await api.unsubscribeCommunityLive('s1');
      await api.relancerCommunityLive();

      expect(mockFetch.mock.calls[0][0]).toBe('./api/community/live/subscribe');
      expect(mockFetch.mock.calls[0][1].method).toBe('POST');
      expect(mockFetch.mock.calls[1][1].body).toBe(JSON.stringify({ session_id: 's1' }));
      expect(mockFetch.mock.calls[2][0]).toBe('./api/community/live/subscribe/s1');
      expect(mockFetch.mock.calls[2][1].method).toBe('DELETE');
      expect(mockFetch.mock.calls[3][0]).toBe('./api/community/live/relancer');
      expect(mockFetch.mock.calls[3][1].method).toBe('POST');
    });
  });
});
