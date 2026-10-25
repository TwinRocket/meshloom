import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import { usePushSubscription } from '../hooks/usePushSubscription';
import i18n from '../i18n';
import fEn from '../i18n/locales/slices/f.en.json';
import fFr from '../i18n/locales/slices/f.fr.json';
import kEn from '../i18n/locales/slices/k.en.json';
import kFr from '../i18n/locales/slices/k.fr.json';
import { DEFAULT_NOTIFICATION_MEDIA } from '../types';

i18n.addResourceBundle('en', 'translation', fEn, true, true);
i18n.addResourceBundle('fr', 'translation', fFr, true, true);
i18n.addResourceBundle('en', 'translation', kEn, true, true);
i18n.addResourceBundle('fr', 'translation', kFr, true, true);

const ALL_ON_DEFAULTS = {
  new_contact: { ...DEFAULT_NOTIFICATION_MEDIA },
  new_dm: { ...DEFAULT_NOTIFICATION_MEDIA },
  advert_repeater: { ...DEFAULT_NOTIFICATION_MEDIA },
  advert_companion: { ...DEFAULT_NOTIFICATION_MEDIA },
  advert_sensor: { ...DEFAULT_NOTIFICATION_MEDIA },
  channel_found: { ...DEFAULT_NOTIFICATION_MEDIA },
  telemetry_alert: { ...DEFAULT_NOTIFICATION_MEDIA },
  oss_update: { ...DEFAULT_NOTIFICATION_MEDIA },
  radio_offline: { ...DEFAULT_NOTIFICATION_MEDIA },
  radio_online: { ...DEFAULT_NOTIFICATION_MEDIA },
  app_started: { ...DEFAULT_NOTIFICATION_MEDIA },
};

const EMPTY_PREFERENCES = {
  defaults: ALL_ON_DEFAULTS,
  overrides: {} as Record<string, boolean>,
  vapid_subject: '',
};

const mocks = vi.hoisted(() => ({
  api: {
    getPushSubscriptions: vi.fn(),
    getPushPreferences: vi.fn(),
    getVapidPublicKey: vi.fn(),
    pushSubscribe: vi.fn(),
    deletePushSubscription: vi.fn(),
    patchPushPreferences: vi.fn(),
    testPushSubscription: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  api: mocks.api,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

function bytesToBase64Url(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

describe('usePushSubscription', () => {
  const vapidOldBytes = [1, 2, 3, 4];
  const vapidNewBytes = [5, 6, 7, 8];
  const oldKey = new Uint8Array(vapidOldBytes).buffer;
  const newKeyBase64 = bytesToBase64Url(vapidNewBytes);

  let activeSubscription: {
    endpoint: string;
    options: { applicationServerKey: ArrayBuffer };
    toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } };
    unsubscribe: ReturnType<typeof vi.fn>;
  } | null;
  let replacementSubscription: {
    endpoint: string;
    options: { applicationServerKey: ArrayBuffer };
    toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } };
    unsubscribe: ReturnType<typeof vi.fn>;
  };
  let getSubscriptionMock: ReturnType<typeof vi.fn>;
  let subscribeMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();

    activeSubscription = {
      endpoint: 'https://push.example.test/sub-old',
      options: { applicationServerKey: oldKey },
      toJSON: () => ({
        endpoint: 'https://push.example.test/sub-old',
        keys: { p256dh: 'p256dh-old', auth: 'auth-old' },
      }),
      unsubscribe: vi.fn(async () => {
        activeSubscription = null;
        return true;
      }),
    };

    replacementSubscription = {
      endpoint: 'https://push.example.test/sub-new',
      options: { applicationServerKey: new Uint8Array(vapidNewBytes).buffer },
      toJSON: () => ({
        endpoint: 'https://push.example.test/sub-new',
        keys: { p256dh: 'p256dh-new', auth: 'auth-new' },
      }),
      unsubscribe: vi.fn(async () => true),
    };

    getSubscriptionMock = vi.fn(async () => activeSubscription);
    subscribeMock = vi.fn(async () => {
      activeSubscription = replacementSubscription;
      return replacementSubscription;
    });

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    Object.defineProperty(window, 'PushManager', {
      configurable: true,
      value: function PushManager() {},
    });
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: function Notification() {},
    });
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: getSubscriptionMock,
            subscribe: subscribeMock,
          },
        }),
      },
    });

    mocks.api.getPushPreferences.mockResolvedValue({ ...EMPTY_PREFERENCES, overrides: {} });
    mocks.api.getPushSubscriptions.mockResolvedValue([
      {
        id: 'sub-1',
        endpoint: 'https://push.example.test/sub-old',
        p256dh: 'p256dh-old',
        auth: 'auth-old',
        label: 'Chrome on macOS',
        created_at: 1,
        last_success_at: null,
        failure_count: 0,
      },
    ]);
    mocks.api.getVapidPublicKey.mockResolvedValue({ public_key: newKeyBase64 });
    mocks.api.pushSubscribe.mockResolvedValue({
      id: 'sub-2',
      endpoint: 'https://push.example.test/sub-new',
    });
  });

  it('clears currentSubscriptionId when refresh no longer finds this browser on the backend', async () => {
    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.currentSubscriptionId).toBe('sub-1');
      expect(result.current.isSubscribed).toBe(true);
    });

    mocks.api.getPushSubscriptions.mockResolvedValueOnce([]);

    await act(async () => {
      await result.current.refreshSubscriptions();
    });

    expect(result.current.currentSubscriptionId).toBeNull();
    expect(result.current.isSubscribed).toBe(false);
    expect(result.current.allSubscriptions).toEqual([]);
  });

  it('times out and shows a toast when service worker never activates', async () => {
    // Replace serviceWorker.ready with a promise that never resolves
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: new Promise(() => {}),
      },
    });

    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.isSupported).toBe(true);
    });

    // subscribe() will hang on serviceWorker.ready, then the 1s timeout fires
    await act(async () => {
      await result.current.subscribe();
    });

    expect(result.current.loading).toBe(false);
    expect(mocks.toast.error).toHaveBeenCalledWith(
      i18n.t('notifications.pushEnableFailed'),
      expect.objectContaining({
        description: expect.stringContaining('trusted TLS certificate for service workers'),
      })
    );
  }, 5_000);

  it('recreates a stale browser subscription when the server VAPID key changed', async () => {
    const oldSubscription = activeSubscription;
    mocks.api.getPushSubscriptions
      .mockReset()
      .mockResolvedValueOnce([
        {
          id: 'sub-1',
          endpoint: 'https://push.example.test/sub-old',
          p256dh: 'p256dh-old',
          auth: 'auth-old',
          label: 'Chrome on macOS',
          created_at: 1,
          last_success_at: null,
          failure_count: 0,
        },
      ])
      .mockResolvedValueOnce([
        {
          id: 'sub-2',
          endpoint: 'https://push.example.test/sub-new',
          p256dh: 'p256dh-new',
          auth: 'auth-new',
          label: 'Chrome on macOS',
          created_at: 2,
          last_success_at: null,
          failure_count: 0,
        },
      ]);

    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.isSupported).toBe(true);
    });

    await act(async () => {
      await result.current.subscribe();
    });

    expect(oldSubscription?.unsubscribe).toHaveBeenCalledTimes(1);
    expect(activeSubscription).toBe(replacementSubscription);
    expect(subscribeMock).toHaveBeenCalledTimes(1);
    expect(mocks.api.pushSubscribe).toHaveBeenCalledWith({
      endpoint: 'https://push.example.test/sub-new',
      p256dh: 'p256dh-new',
      auth: 'auth-new',
      label: expect.any(String),
      language: expect.any(String),
    });
    expect(result.current.currentSubscriptionId).toBe('sub-2');
  });

  it('loads the push preferences on mount', async () => {
    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.preferences).toEqual(EMPTY_PREFERENCES);
    });
  });

  it('patches defaults and vapid_subject', async () => {
    const updated = {
      defaults: { ...ALL_ON_DEFAULTS, new_dm: { push: false, email: false, webhook: false } },
      overrides: {},
      vapid_subject: 'mailto:ops@example.com',
    };
    mocks.api.patchPushPreferences.mockResolvedValue(updated);

    const { result } = renderHook(() => usePushSubscription());
    await waitFor(() => {
      expect(result.current.preferences).not.toBeNull();
    });

    await act(async () => {
      await result.current.patchPreferences({
        defaults: { new_dm: { push: false } },
        vapid_subject: 'mailto:ops@example.com',
      });
    });

    expect(mocks.api.patchPushPreferences).toHaveBeenCalledWith({
      defaults: { new_dm: { push: false } },
      vapid_subject: 'mailto:ops@example.com',
    });
    expect(result.current.preferences).toEqual(updated);
  });

  it('shows the translated code of a failed test push, never the backend text', async () => {
    mocks.api.testPushSubscription.mockRejectedValue(
      new ApiError('Push delivery failed', 422, {
        code: 'push_delivery_failed',
        message: 'Push delivery failed',
      })
    );

    const { result } = renderHook(() => usePushSubscription());
    await act(async () => {
      await result.current.testPush('sub-1');
    });

    expect(mocks.toast.error).toHaveBeenCalledWith(i18n.t('notifications.pushTestFailed'), {
      description: i18n.t('errors.push_delivery_failed'),
    });
  });
});
