import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import { RAIL_OVERLAY_BACKFILL_KEY } from '../components/navDestinations';
import { useAppSettings } from '../hooks/useAppSettings';
import type { AppSettings } from '../types';

const mocks = vi.hoisted(() => ({
  api: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
    toggleBlockedKey: vi.fn(),
    toggleBlockedName: vi.fn(),
    toggleTrackedTelemetry: vi.fn(),
    toggleTrackedTelemetryContact: vi.fn(),
    toggleFavorite: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  takePrefetchOrFetch: vi.fn(),
}));

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    api: mocks.api,
  };
});

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

vi.mock('../prefetch', () => ({
  takePrefetchOrFetch: mocks.takePrefetchOrFetch,
}));

describe('useAppSettings telemetry toasts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the backend 409 message when repeater telemetry is full', async () => {
    mocks.api.toggleTrackedTelemetry.mockRejectedValue(
      new ApiError('Limit of 8 tracked repeaters reached', 409, {
        message: 'Limit of 8 tracked repeaters reached',
        tracked_telemetry_repeaters: [],
      })
    );

    const { result } = renderHook(() => useAppSettings());
    await act(async () => {
      await result.current.handleToggleTrackedTelemetry('aa'.repeat(32));
    });

    expect(mocks.toast.error).toHaveBeenCalledWith('Limit of 8 tracked repeaters reached');
  });

  it('shows the backend 409 message when contact telemetry is full', async () => {
    mocks.api.toggleTrackedTelemetryContact.mockRejectedValue(
      new ApiError('Limit of 8 tracked contacts reached', 409, {
        message: 'Limit of 8 tracked contacts reached',
      })
    );

    const { result } = renderHook(() => useAppSettings());
    await act(async () => {
      await result.current.handleToggleTrackedTelemetryContact('bb'.repeat(32));
    });

    expect(mocks.toast.error).toHaveBeenCalledWith('Limit of 8 tracked contacts reached');
  });
});

const settingsFixture = (nav_rail: string[]): AppSettings => ({
  ui_preferences: { nav_rail, theme: '' },
  max_radio_contacts: 200,
  auto_decrypt_dm_on_advert: false,
  last_message_times: {},
  advert_interval: 0,
  last_advert_time: 0,
  flood_scope: '',
  known_regions: [],
  blocked_keys: [],
  blocked_names: [],
  discovery_blocked_types: [],
  tracked_telemetry_repeaters: [],
  tracked_telemetry_contacts: [],
  auto_resend_channel: false,
  telemetry_interval_hours: 8,
  telemetry_routed_hourly: false,
});

describe('useAppSettings rail overlay backfill', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem(RAIL_OVERLAY_BACKFILL_KEY);
  });

  it('appends the channel finder to a stored rail once', async () => {
    const stored = ['conversations', 'map', 'live'];
    mocks.takePrefetchOrFetch.mockResolvedValue(settingsFixture(stored));
    mocks.api.updateSettings.mockResolvedValue({});

    const { result } = renderHook(() => useAppSettings());
    await act(async () => {
      await result.current.fetchAppSettings();
    });

    expect(result.current.appSettings?.ui_preferences.nav_rail).toEqual([...stored, 'discovered']);
    expect(mocks.api.updateSettings).toHaveBeenCalledWith({
      ui_preferences: { theme: '', nav_rail: [...stored, 'discovered'] },
    });
    expect(localStorage.getItem(RAIL_OVERLAY_BACKFILL_KEY)).toBe('1');
  });

  it('does not re-add the overlay after the user removed it', async () => {
    localStorage.setItem(RAIL_OVERLAY_BACKFILL_KEY, '1');
    const stored = ['conversations', 'map', 'live'];
    mocks.takePrefetchOrFetch.mockResolvedValue(settingsFixture(stored));

    const { result } = renderHook(() => useAppSettings());
    await act(async () => {
      await result.current.fetchAppSettings();
    });

    expect(result.current.appSettings?.ui_preferences.nav_rail).toEqual(stored);
    expect(mocks.api.updateSettings).not.toHaveBeenCalled();
  });
});
