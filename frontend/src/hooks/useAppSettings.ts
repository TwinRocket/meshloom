import { useState, useCallback, useEffect, useRef } from 'react';
import { api, formatApiError } from '../api';
import { takePrefetchOrFetch } from '../prefetch';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import { initLastMessageTimes } from '../utils/conversationState';
import { applyTheme, cacheTheme, getSavedTheme, serverChoseTheme } from '../utils/theme';
import type { AppSettings, AppSettingsUpdate } from '../types';
import { RAIL_OVERLAY_BACKFILL_KEY, backfillRailOverlaysOnce } from '../components/navDestinations';

function railOverlayAlreadyBackfilled(): boolean {
  try {
    return localStorage.getItem(RAIL_OVERLAY_BACKFILL_KEY) === '1';
  } catch {
    return false;
  }
}

function markRailOverlayBackfilled(): void {
  try {
    localStorage.setItem(RAIL_OVERLAY_BACKFILL_KEY, '1');
  } catch {
    // Private mode or quota — the next load may backfill again.
  }
}

/**
 * Take the instance's theme, and keep a local copy of it.
 *
 * The copy is what makes the flash happen once on a device rather than on every
 * load: the next visit paints from it before anything is fetched. It is a cache,
 * never the authority — the stored value wins whenever the two disagree, which is
 * how a theme picked on one device reaches the others.
 *
 * Skipped entirely when the server wrote the theme into the page, since there is
 * then nothing to correct and no frame in which the wrong one was shown.
 */
function adoptStoredTheme(stored: string | undefined): void {
  if (stored === undefined) return;
  if (serverChoseTheme()) {
    // Still cached, so a later load served by something other than the backend
    // starts from the right theme instead of the default.
    cacheTheme(stored);
    return;
  }
  if (stored === getSavedTheme()) return;
  applyTheme(stored);
}

export function useAppSettings() {
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);

  // One-time migration guard
  const hasMigratedRef = useRef(false);

  const fetchAppSettings = useCallback(async () => {
    try {
      const data = await takePrefetchOrFetch('settings', api.getSettings);
      const alreadyBackfilled = railOverlayAlreadyBackfilled();
      const backfill = backfillRailOverlaysOnce(data.ui_preferences?.nav_rail, alreadyBackfilled);
      const next = backfill.shouldPersist
        ? {
            ...data,
            ui_preferences: {
              theme: data.ui_preferences?.theme ?? '',
              nav_rail: backfill.ids,
            },
          }
        : data;
      setAppSettings(next);
      initLastMessageTimes(data.last_message_times ?? {});
      adoptStoredTheme(data.ui_preferences?.theme);
      if (backfill.shouldPersist) {
        try {
          await api.updateSettings({ ui_preferences: next.ui_preferences });
          markRailOverlayBackfilled();
        } catch (err) {
          console.error('Failed to backfill desktop rail:', err);
        }
      } else if (!alreadyBackfilled) {
        markRailOverlayBackfilled();
      }
    } catch (err) {
      console.error('Failed to fetch app settings:', err);
    }
  }, []);

  const handleSaveAppSettings = useCallback(
    async (update: AppSettingsUpdate) => {
      await api.updateSettings(update);
      await fetchAppSettings();
    },
    [fetchAppSettings]
  );

  const handleToggleBlockedKey = useCallback(async (key: string) => {
    const normalizedKey = key.toLowerCase();
    setAppSettings((prev) => {
      if (!prev) return prev;
      const current = prev.blocked_keys ?? [];
      const wasBlocked = current.includes(normalizedKey);
      const optimistic = wasBlocked
        ? current.filter((k) => k !== normalizedKey)
        : [...current, normalizedKey];
      return { ...prev, blocked_keys: optimistic };
    });

    try {
      const updatedSettings = await api.toggleBlockedKey(key);
      setAppSettings(updatedSettings);
    } catch (err) {
      console.error('Failed to toggle blocked key:', err);
      try {
        const settings = await api.getSettings();
        setAppSettings(settings);
      } catch {
        // If refetch also fails, leave optimistic state
      }
      toast.error(i18n.t('toast.blockedKeyFailed'));
    }
  }, []);

  const handleToggleBlockedName = useCallback(async (name: string) => {
    setAppSettings((prev) => {
      if (!prev) return prev;
      const current = prev.blocked_names ?? [];
      const wasBlocked = current.includes(name);
      const optimistic = wasBlocked ? current.filter((n) => n !== name) : [...current, name];
      return { ...prev, blocked_names: optimistic };
    });

    try {
      const updatedSettings = await api.toggleBlockedName(name);
      setAppSettings(updatedSettings);
    } catch (err) {
      console.error('Failed to toggle blocked name:', err);
      try {
        const settings = await api.getSettings();
        setAppSettings(settings);
      } catch {
        // If refetch also fails, leave optimistic state
      }
      toast.error(i18n.t('toast.blockedNameFailed'));
    }
  }, []);

  const handleToggleTrackedTelemetry = useCallback(async (publicKey: string) => {
    const key = publicKey.toLowerCase();
    setAppSettings((prev) => {
      if (!prev) return prev;
      const current = prev.tracked_telemetry_repeaters ?? [];
      const wasTracked = current.includes(key);
      const optimistic = wasTracked ? current.filter((k) => k !== key) : [...current, key];
      return { ...prev, tracked_telemetry_repeaters: optimistic };
    });

    try {
      const result = await api.toggleTrackedTelemetry(publicKey);
      const settings = await api.getSettings();
      setAppSettings({
        ...settings,
        tracked_telemetry_repeaters: result.tracked_telemetry_repeaters,
      });
    } catch (err) {
      console.error('Failed to toggle tracked telemetry:', err);
      try {
        const settings = await api.getSettings();
        setAppSettings(settings);
      } catch {
        // If refetch also fails, leave optimistic state
      }
      toast.error(formatApiError(err, i18n.t) || i18n.t('toast.trackedTelemetryFailed'));
    }
  }, []);

  const handleToggleTrackedTelemetryContact = useCallback(async (publicKey: string) => {
    const key = publicKey.toLowerCase();
    setAppSettings((prev) => {
      if (!prev) return prev;
      const current = prev.tracked_telemetry_contacts ?? [];
      const wasTracked = current.includes(key);
      const optimistic = wasTracked ? current.filter((k) => k !== key) : [...current, key];
      return { ...prev, tracked_telemetry_contacts: optimistic };
    });

    try {
      const result = await api.toggleTrackedTelemetryContact(publicKey);
      const settings = await api.getSettings();
      setAppSettings({
        ...settings,
        tracked_telemetry_contacts: result.tracked_telemetry_contacts,
      });
    } catch (err) {
      console.error('Failed to toggle tracked contact telemetry:', err);
      try {
        const settings = await api.getSettings();
        setAppSettings(settings);
      } catch {
        // If refetch also fails, leave optimistic state
      }
      toast.error(formatApiError(err, i18n.t) || i18n.t('toast.trackedContactTelemetryFailed'));
    }
  }, []);

  // Legacy favorites migration: if pre-server-side favorites exist in
  // localStorage, toggle each one via the existing API and clear the key.
  useEffect(() => {
    if (!appSettings || hasMigratedRef.current) return;
    hasMigratedRef.current = true;

    const FAVORITES_KEY = 'meshloom-favorites';
    let localFavorites: Array<{ type: 'channel' | 'contact'; id: string }> = [];
    try {
      const stored = localStorage.getItem(FAVORITES_KEY);
      if (stored) localFavorites = JSON.parse(stored);
    } catch {
      // corrupt or unavailable
    }
    if (localFavorites.length === 0) return;

    const migrate = async () => {
      let migrated = 0;
      for (const f of localFavorites) {
        try {
          await api.toggleFavorite(f.type, f.id);
          migrated++;
        } catch {
          // Entity may have been deleted; skip and continue
        }
      }
      localStorage.removeItem(FAVORITES_KEY);
      // Reload so contacts/channels pick up the new favorite flags
      if (migrated > 0) window.location.reload();
    };
    migrate();
  }, [appSettings]);

  return {
    appSettings,
    fetchAppSettings,
    handleSaveAppSettings,
    handleToggleBlockedKey,
    handleToggleBlockedName,
    handleToggleTrackedTelemetry,
    handleToggleTrackedTelemetryContact,
  };
}
