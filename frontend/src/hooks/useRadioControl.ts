import { useState, useCallback, useEffect, useRef } from 'react';
import { api, formatApiError } from '../api';
import { takePrefetchOrFetch } from '../prefetch';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import type {
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioReconnectResponse,
  RadioRegionDiscoveryResponse,
} from '../types';

export function useRadioControl() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [meshDiscovery, setMeshDiscovery] = useState<RadioDiscoveryResponse | null>(null);
  const [meshDiscoveryLoadingTarget, setMeshDiscoveryLoadingTarget] =
    useState<RadioDiscoveryTarget | null>(null);
  const [regionDiscovery, setRegionDiscovery] = useState<RadioRegionDiscoveryResponse | null>(null);
  const [regionDiscoveryLoading, setRegionDiscoveryLoading] = useState(false);

  const prevHealthRef = useRef<HealthStatus | null>(null);
  const rebootPollTokenRef = useRef(0);

  // Cancel any in-flight reboot polling on unmount
  useEffect(() => {
    return () => {
      rebootPollTokenRef.current += 1;
    };
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await takePrefetchOrFetch('config', api.getRadioConfig);
      setConfig(data);
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  }, []);

  const handleSaveConfig = useCallback(
    async (update: RadioConfigUpdate) => {
      const saved = await api.updateRadioConfig(update);
      if (saved?.warnings?.includes('client_repeat_not_allowed')) {
        toast.warning(i18n.t('settings.radio.clientRepeatDropped'));
      }
      await fetchConfig();
    },
    [fetchConfig]
  );

  const handleSetPrivateKey = useCallback(
    async (key: string) => {
      await api.setPrivateKey(key);
      await fetchConfig();
    },
    [fetchConfig]
  );

  const handleReboot = useCallback(async () => {
    await api.rebootRadio();
    setHealth((prev) =>
      prev ? { ...prev, radio_connected: false, radio_initializing: false } : prev
    );
    const pollToken = ++rebootPollTokenRef.current;
    const pollUntilReconnected = async () => {
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (rebootPollTokenRef.current !== pollToken) return;
        try {
          const data = await api.getHealth();
          if (rebootPollTokenRef.current !== pollToken) return;
          setHealth(data);
          if (data.radio_connected) {
            fetchConfig();
            return;
          }
        } catch {
          // Keep polling
        }
      }
    };
    pollUntilReconnected();
  }, [fetchConfig]);

  const handleDisconnect = useCallback(async () => {
    await api.disconnectRadio();
    const pausedHealth = await api.getHealth();
    setHealth(pausedHealth);
  }, []);

  const handleReconnect = useCallback(async (): Promise<RadioReconnectResponse> => {
    const result = await api.reconnectRadio();
    const refreshedHealth = await api.getHealth();
    setHealth(refreshedHealth);
    if (refreshedHealth.radio_connected) {
      await fetchConfig();
    }
    return result;
  }, [fetchConfig]);

  const handleAdvertise = useCallback(async (mode: RadioAdvertMode = 'flood') => {
    try {
      await api.sendAdvertisement(mode);
      toast.success(
        mode === 'zero_hop' ? i18n.t('toast.advertZeroHopSent') : i18n.t('toast.advertSent')
      );
    } catch (err) {
      const label =
        mode === 'zero_hop' ? i18n.t('toast.advertZeroHopLabel') : i18n.t('toast.advertLabel');
      console.error(`Failed to send ${label}:`, err);
      toast.error(i18n.t('toast.advertFailed', { label }), {
        description: formatApiError(err, i18n.t) || i18n.t('chat.checkRadio'),
      });
    }
  }, []);

  const handleDiscoverMesh = useCallback(async (target: RadioDiscoveryTarget) => {
    setMeshDiscoveryLoadingTarget(target);
    try {
      const data = await api.discoverMesh(target);
      setMeshDiscovery(data);
      toast.success(
        data.results.length === 0
          ? i18n.t('toast.discoverNone')
          : i18n.t('toast.discoverFound', { count: data.results.length })
      );
    } catch (err) {
      console.error('Failed to discover nearby nodes:', err);
      toast.error(i18n.t('toast.discoverFailed'), {
        description: formatApiError(err, i18n.t) || i18n.t('chat.checkRadio'),
      });
    } finally {
      setMeshDiscoveryLoadingTarget(null);
    }
  }, []);

  const handleDiscoverRegions = useCallback(async (publicKeys?: string[]) => {
    setRegionDiscoveryLoading(true);
    try {
      const data = await api.discoverRegions(publicKeys);
      setRegionDiscovery(data);
      if (data.repeaters_queried === 0) {
        toast.info(i18n.t('toast.regionsNoneAvailable'));
      } else if (data.regions.length === 0) {
        toast.info(
          i18n.t('toast.regionsNoneReported', {
            answered: data.repeaters_answered,
            queried: data.repeaters_queried,
          })
        );
      } else {
        toast.success(
          i18n.t('toast.regionsFound', {
            count: data.regions.length,
            answered: data.repeaters_answered,
            queried: data.repeaters_queried,
          })
        );
      }
    } catch (err) {
      console.error('Failed to discover regions:', err);
      toast.error(i18n.t('toast.regionsFailed'), {
        description: formatApiError(err, i18n.t) || i18n.t('chat.checkRadio'),
      });
    } finally {
      setRegionDiscoveryLoading(false);
    }
  }, []);

  const handleHealthRefresh = useCallback(async () => {
    try {
      const data = await api.getHealth();
      setHealth(data);
    } catch (err) {
      console.error('Failed to refresh health:', err);
    }
  }, []);

  return {
    health,
    setHealth,
    config,
    setConfig,
    prevHealthRef,
    fetchConfig,
    handleSaveConfig,
    handleSetPrivateKey,
    handleReboot,
    handleDisconnect,
    handleReconnect,
    handleAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    handleDiscoverMesh,
    regionDiscovery,
    regionDiscoveryLoading,
    handleDiscoverRegions,
    handleHealthRefresh,
  };
}
