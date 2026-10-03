import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { api } from '../api';
import type { RadioRecord } from '../types';

export const ACTIVE_RADIO_STORAGE_KEY = 'meshloom_active_radio_id';

export interface RadioContextValue {
  radios: RadioRecord[];
  activeRadioId: string;
  setActiveRadioId: (id: string) => void;
  activeRadio: RadioRecord | undefined;
  refreshRadios: () => Promise<RadioRecord[]>;
  isLoading: boolean;
  handleRadioCreated: (radio: RadioRecord) => void;
  handleRadioUpdated: (radio: RadioRecord) => void;
  handleRadioDeleted: (radioId: string) => void;
}

export const defaultRadioContextValue: RadioContextValue = {
  radios: [],
  activeRadioId: 'default',
  setActiveRadioId: () => {},
  activeRadio: undefined,
  refreshRadios: async () => [],
  isLoading: false,
  handleRadioCreated: () => {},
  handleRadioUpdated: () => {},
  handleRadioDeleted: () => {},
};

export const RadioContext = createContext<RadioContextValue>(defaultRadioContextValue);

interface RadioProviderProps {
  children: ReactNode;
  initialRadios?: RadioRecord[];
  initialActiveRadioId?: string;
}

export function RadioProvider({
  children,
  initialRadios,
  initialActiveRadioId,
}: RadioProviderProps) {
  const [radios, setRadios] = useState<RadioRecord[]>(initialRadios ?? []);
  const [isLoading, setIsLoading] = useState(false);

  const [activeRadioId, setActiveRadioIdState] = useState<string>(() => {
    if (initialActiveRadioId) return initialActiveRadioId;
    try {
      const stored = localStorage.getItem(ACTIVE_RADIO_STORAGE_KEY);
      if (stored && stored.trim()) return stored.trim();
    } catch {
      // LocalStorage unavailable
    }
    return 'default';
  });

  const setActiveRadioId = useCallback((id: string) => {
    const trimmed = id.trim();
    if (!trimmed) return;
    setActiveRadioIdState(trimmed);
    try {
      localStorage.setItem(ACTIVE_RADIO_STORAGE_KEY, trimmed);
    } catch {
      // LocalStorage unavailable
    }
  }, []);

  const refreshRadios = useCallback(async (): Promise<RadioRecord[]> => {
    setIsLoading(true);
    try {
      const fetched = await api.getRadios();
      setRadios(fetched);
      return fetched;
    } catch (err) {
      console.error('Failed to load radios list:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Fetch radios on mount if not provided as initial
  const hasFetchedRef = useRef(false);
  useEffect(() => {
    if (initialRadios && initialRadios.length > 0) return;
    if (hasFetchedRef.current) return;
    hasFetchedRef.current = true;
    void refreshRadios();
  }, [initialRadios, refreshRadios]);

  // Ensure activeRadioId points to a valid radio once radios list is loaded
  useEffect(() => {
    if (radios.length === 0) return;
    const exists = radios.some((r) => r.id === activeRadioId);
    if (!exists) {
      const fallback = radios.some((r) => r.id === 'default') ? 'default' : radios[0].id;
      setActiveRadioId(fallback);
    }
  }, [radios, activeRadioId, setActiveRadioId]);

  const activeRadio = useMemo(() => {
    return (
      radios.find((r) => r.id === activeRadioId) ?? (radios.length > 0 ? radios[0] : undefined)
    );
  }, [radios, activeRadioId]);

  const handleRadioCreated = useCallback((radio: RadioRecord) => {
    setRadios((prev) => {
      const exists = prev.some((r) => r.id === radio.id);
      if (exists) {
        return prev.map((r) => (r.id === radio.id ? radio : r));
      }
      return [...prev, radio].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    });
  }, []);

  const handleRadioUpdated = useCallback((radio: RadioRecord) => {
    setRadios((prev) => prev.map((r) => (r.id === radio.id ? radio : r)));
  }, []);

  const handleRadioDeleted = useCallback(
    (radioId: string) => {
      setRadios((prev) => {
        const next = prev.filter((r) => r.id !== radioId);
        if (activeRadioId === radioId) {
          const fallback = next.some((r) => r.id === 'default')
            ? 'default'
            : next.length > 0
              ? next[0].id
              : 'default';
          setActiveRadioId(fallback);
        }
        return next;
      });
    },
    [activeRadioId, setActiveRadioId]
  );

  // Listen to custom window events for loose coupling with WebSocket listener
  useEffect(() => {
    const onCreated = (e: Event) => {
      const detail = (e as CustomEvent<RadioRecord>).detail;
      if (detail) handleRadioCreated(detail);
    };
    const onUpdated = (e: Event) => {
      const detail = (e as CustomEvent<RadioRecord>).detail;
      if (detail) handleRadioUpdated(detail);
    };
    const onDeleted = (e: Event) => {
      const detail = (e as CustomEvent<{ radio_id: string }>).detail;
      if (detail?.radio_id) handleRadioDeleted(detail.radio_id);
    };

    window.addEventListener('meshloom_ws_radio_created', onCreated);
    window.addEventListener('meshloom_ws_radio_updated', onUpdated);
    window.addEventListener('meshloom_ws_radio_deleted', onDeleted);

    return () => {
      window.removeEventListener('meshloom_ws_radio_created', onCreated);
      window.removeEventListener('meshloom_ws_radio_updated', onUpdated);
      window.removeEventListener('meshloom_ws_radio_deleted', onDeleted);
    };
  }, [handleRadioCreated, handleRadioUpdated, handleRadioDeleted]);

  const contextValue = useMemo<RadioContextValue>(
    () => ({
      radios,
      activeRadioId,
      setActiveRadioId,
      activeRadio,
      refreshRadios,
      isLoading,
      handleRadioCreated,
      handleRadioUpdated,
      handleRadioDeleted,
    }),
    [
      radios,
      activeRadioId,
      setActiveRadioId,
      activeRadio,
      refreshRadios,
      isLoading,
      handleRadioCreated,
      handleRadioUpdated,
      handleRadioDeleted,
    ]
  );

  return <RadioContext.Provider value={contextValue}>{children}</RadioContext.Provider>;
}

export function useRadioContext(): RadioContextValue {
  return useContext(RadioContext);
}
