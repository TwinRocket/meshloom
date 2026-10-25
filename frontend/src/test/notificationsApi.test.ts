/** The notifications API client (`/api/notifications`) and the error copy of its codes. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api, formatApiError } from '../api';
import i18n from '../i18n';
import { WS_EVENT_PERMISSIONS } from '../wsEventTypes';

const mockFetch = vi.fn();

function reply(body: unknown) {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(body) });
  global.fetch = mockFetch;
}

describe('notifications client', () => {
  afterEach(() => vi.restoreAllMocks());

  it('reads types, events, channels and rules', async () => {
    reply([]);
    await api.getNotificationTypes();
    await api.getNotificationChannels();
    await api.getNotificationRules();
    await api.getNotificationEvents();
    expect(mockFetch.mock.calls.map((c) => c[0])).toEqual([
      './api/notifications/types',
      './api/notifications/channels',
      './api/notifications/rules',
      './api/notifications/events',
    ]);
  });

  it('creates, patches, tests and deletes a channel', async () => {
    reply({});
    await api.createNotificationChannel({ type: 'webhook', name: 'Hook', config: { url: 'u' } });
    await api.updateNotificationChannel('webhook-1', { config: { url: '********' } });
    await api.testNotificationConfig({ type: 'webhook', name: 'Hook', config: { url: 'u' } });
    await api.testNotificationChannel('builtin:push');
    await api.deleteNotificationChannel('webhook-1');
    const calls = mockFetch.mock.calls.map(([url, init]) => [url, init?.method]);
    expect(calls).toEqual([
      ['./api/notifications/channels', 'POST'],
      ['./api/notifications/channels/webhook-1', 'PATCH'],
      ['./api/notifications/channels/test', 'POST'],
      ['./api/notifications/channels/builtin%3Apush/test', 'POST'],
      ['./api/notifications/channels/webhook-1', 'DELETE'],
    ]);
    expect(mockFetch.mock.calls[1][1].body).toBe(JSON.stringify({ config: { url: '********' } }));
  });

  it('puts rules and builds the journal query', async () => {
    reply({});
    await api.patchNotificationRules({ paused_until: null });
    await api.setNotificationConversationRule('contact-ab', { 'builtin:push': false });
    await api.setNotificationConversationRule('contact-ab', null);
    await api.getNotificationDeliveries({ channel: 'webhook-1', limit: 20 });
    await api.getNotificationDeliveries();
    expect(mockFetch.mock.calls[0][1].body).toBe(JSON.stringify({ paused_until: null }));
    expect(mockFetch.mock.calls[1][0]).toBe('./api/notifications/rules/conversations/contact-ab');
    expect(mockFetch.mock.calls[2][1].body).toBe(JSON.stringify({ channels: null }));
    expect(mockFetch.mock.calls[3][0]).toBe(
      './api/notifications/deliveries?channel=webhook-1&limit=20'
    );
    expect(mockFetch.mock.calls[4][0]).toBe('./api/notifications/deliveries');
  });

  it('knows the admin-only channel change event', () => {
    expect(WS_EVENT_PERMISSIONS.notification_channel_changed).toBe('admin');
  });
});

describe('notification error codes', () => {
  const codes = [
    'invalid_config',
    'auth_failed',
    'rate_limited',
    'quota_exceeded',
    'channel_paused',
    'channel_missing',
    'builtin_channel',
    'type_immutable',
    'service_error',
    'timeout',
    'tls_error',
    'egress_private_address',
  ];

  it.each(['en', 'fr'])('translates every code in %s', async (lang) => {
    await i18n.changeLanguage(lang);
    for (const code of codes) {
      const text = formatApiError(new ApiError('raw', 400, { code, params: {} }), i18n.t);
      expect(text, code).not.toBe('raw');
      expect(text, code).not.toContain('errors.');
    }
    await i18n.changeLanguage('en');
  });
});
