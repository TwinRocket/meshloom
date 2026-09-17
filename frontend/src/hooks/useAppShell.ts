import { startTransition, useCallback, useEffect, useRef, useState } from 'react';

import { getLocalLabel, type LocalLabel } from '../utils/localLabel';
import { getSavedDistanceUnit, type DistanceUnit } from '../utils/distanceUnits';
import { getSavedRenderRichPayloads } from '../utils/richPayloadPreference';
import { getSavedShowPathHopWidth } from '../utils/pathHopWidthPreference';
import {
  getSavedDesktopSidebarCollapsed,
  setSavedDesktopSidebarCollapsed,
} from '../utils/sidebarRailPreference';
import type { SettingsSection } from '../components/settings/settingsConstants';
import { parseHashSettingsSection, updateSettingsHash, pushSettingsHash } from '../utils/urlHash';

interface UseAppShellResult {
  showNewMessage: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  desktopSidebarCollapsed: boolean;
  showCracker: boolean;
  crackerRunning: boolean;
  localLabel: LocalLabel;
  distanceUnit: DistanceUnit;
  renderRichPayloads: boolean;
  showPathHopWidth: boolean;
  setSettingsSection: (section: SettingsSection) => void;
  setSidebarOpen: (open: boolean) => void;
  setCrackerRunning: (running: boolean) => void;
  setLocalLabel: (label: LocalLabel) => void;
  setDistanceUnit: (unit: DistanceUnit) => void;
  setRenderRichPayloads: (enabled: boolean) => void;
  setShowPathHopWidth: (enabled: boolean) => void;
  handleCloseSettingsView: (restoreLocation?: boolean) => void;
  handleToggleSettingsView: () => void;
  handleOpenNewMessage: () => void;
  handleCloseNewMessage: () => void;
  handleToggleCracker: () => void;
  handleToggleDesktopSidebar: () => void;
  handleExpandDesktopSidebar: () => void;
}

export function useAppShell(): UseAppShellResult {
  const initialSettingsSection = typeof window === 'undefined' ? null : parseHashSettingsSection();
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(() => initialSettingsSection !== null);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(
    () => initialSettingsSection ?? 'radio'
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(
    getSavedDesktopSidebarCollapsed
  );
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);
  const [localLabel, setLocalLabel] = useState(getLocalLabel);
  const [distanceUnit, setDistanceUnit] = useState(getSavedDistanceUnit);
  const [renderRichPayloads, setRenderRichPayloads] = useState(getSavedRenderRichPayloads);
  const [showPathHopWidth, setShowPathHopWidth] = useState(getSavedShowPathHopWidth);
  const previousHashRef = useRef('');
  const isOpeningSettingsRef = useRef(false);
  const pushedSettingsEntryRef = useRef(false);

  useEffect(() => {
    if (showSettings) {
      if (isOpeningSettingsRef.current) {
        pushSettingsHash(settingsSection);
        isOpeningSettingsRef.current = false;
      } else {
        updateSettingsHash(settingsSection);
      }
    }
  }, [settingsSection, showSettings]);

  /**
   * Leave settings.
   *
   * `restoreLocation` puts back whatever was open before, which is what closing
   * settings means. It must not happen when the reader is leaving *for* somewhere
   * else: the restoration goes through `history.back()`, whose popstate lands
   * after the new destination has been set and quietly replaces it — clicking
   * Conversations from settings returned to the tool that was open beforehand.
   */
  const handleCloseSettingsView = useCallback((restoreLocation = true) => {
    startTransition(() => setShowSettings(false));
    setSidebarOpen(false);
    if (typeof window === 'undefined') return;
    if (!restoreLocation) {
      pushedSettingsEntryRef.current = false;
      if (parseHashSettingsSection() !== null) {
        window.history.replaceState(null, '', window.location.pathname);
      }
      return;
    }
    if (pushedSettingsEntryRef.current) {
      pushedSettingsEntryRef.current = false;
      window.history.back();
    } else if (parseHashSettingsSection() !== null) {
      window.history.replaceState(null, '', previousHashRef.current || window.location.pathname);
    }
  }, []);

  const handleToggleSettingsView = useCallback(() => {
    if (showSettings) {
      handleCloseSettingsView();
      return;
    }

    if (typeof window !== 'undefined') {
      previousHashRef.current =
        parseHashSettingsSection() === null ? window.location.hash : previousHashRef.current;
    }
    isOpeningSettingsRef.current = true;
    pushedSettingsEntryRef.current = true;
    startTransition(() => {
      setShowSettings(true);
    });
    setSidebarOpen(false);
  }, [handleCloseSettingsView, showSettings]);

  // Respond to browser back/forward navigating into or out of settings
  useEffect(() => {
    const handlePopstate = () => {
      const section = parseHashSettingsSection();
      if (section !== null) {
        // Don't set pushedSettingsEntryRef here — the user arrived via
        // back/forward, not by opening settings.  Closing settings should
        // replaceState, not history.back(), to avoid popping an unrelated entry.
        startTransition(() => {
          setShowSettings(true);
          setSettingsSection(section);
        });
      } else {
        startTransition(() => setShowSettings(false));
      }
    };

    window.addEventListener('popstate', handlePopstate);
    return () => window.removeEventListener('popstate', handlePopstate);
  }, []);

  const handleOpenNewMessage = useCallback(() => {
    setShowNewMessage(true);
    setSidebarOpen(false);
  }, []);

  const handleCloseNewMessage = useCallback(() => {
    setShowNewMessage(false);
  }, []);

  const handleToggleCracker = useCallback(() => {
    setShowCracker((prev) => !prev);
  }, []);

  const handleToggleDesktopSidebar = useCallback(() => {
    setDesktopSidebarCollapsed((prev) => {
      const next = !prev;
      setSavedDesktopSidebarCollapsed(next);
      return next;
    });
  }, []);

  const handleExpandDesktopSidebar = useCallback(() => {
    setDesktopSidebarCollapsed(false);
    setSavedDesktopSidebarCollapsed(false);
  }, []);

  return {
    showNewMessage,
    showSettings,
    settingsSection,
    sidebarOpen,
    desktopSidebarCollapsed,
    showCracker,
    crackerRunning,
    localLabel,
    distanceUnit,
    renderRichPayloads,
    showPathHopWidth,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
    setLocalLabel,
    setDistanceUnit,
    setRenderRichPayloads,
    setShowPathHopWidth,
    handleCloseSettingsView,
    handleToggleSettingsView,
    handleOpenNewMessage,
    handleCloseNewMessage,
    handleToggleCracker,
    handleToggleDesktopSidebar,
    handleExpandDesktopSidebar,
  };
}
