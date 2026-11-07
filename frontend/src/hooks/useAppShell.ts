import { startTransition, useCallback, useEffect, useRef, useState } from 'react';

import { getSavedDistanceUnit, type DistanceUnit } from '../utils/distanceUnits';
import { getSavedRenderRichPayloads } from '../utils/richPayloadPreference';
import { getSavedShowPathHopWidth } from '../utils/pathHopWidthPreference';
import {
  getSavedDesktopSidebarCollapsed,
  setSavedDesktopSidebarCollapsed,
} from '../utils/sidebarRailPreference';
import { useCapabilities } from '../auth/SessionProvider';
import {
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from '../components/settings/settingsConstants';
import { visibleSettingsSections } from '../auth/capabilities';
import {
  parseHashSettingsSection,
  pushSettingsHash,
  rememberSettingsAnchor,
  updateSettingsHash,
} from '../utils/urlHash';

interface UseAppShellResult {
  showNewMessage: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  desktopSidebarCollapsed: boolean;
  showCracker: boolean;
  crackerRunning: boolean;
  distanceUnit: DistanceUnit;
  renderRichPayloads: boolean;
  showPathHopWidth: boolean;
  setSettingsSection: (section: SettingsSection) => void;
  setSidebarOpen: (open: boolean) => void;
  setCrackerRunning: (running: boolean) => void;
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
  const capabilities = useCapabilities();
  // Read once: the hash is rewritten to `#settings/<section>` by the first
  // commit, and App re-renders long before a lazy section mounts to take the anchor.
  const [initialSettingsSection] = useState<SettingsSection | null>(() => {
    const section = typeof window === 'undefined' ? null : parseHashSettingsSection();
    if (section !== null) rememberSettingsAnchor();
    return section;
  });
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettingsState, setShowSettings] = useState(() => initialSettingsSection !== null);
  const [settingsSectionState, setSettingsSection] = useState<SettingsSection>(
    () => initialSettingsSection ?? 'radio'
  );
  // The interface never opens a page the role cannot use (the server refuses it anyway):
  // a hash or a stored section outside the role lands on its first section, and a role
  // with no settings (the kiosk) never shows them.
  const allowedSections = visibleSettingsSections(capabilities, SETTINGS_SECTION_ORDER);
  const showSettings = showSettingsState && allowedSections.length > 0;
  const settingsSection = allowedSections.includes(settingsSectionState)
    ? settingsSectionState
    : (allowedSections[0] ?? settingsSectionState);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(
    getSavedDesktopSidebarCollapsed
  );
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);
  const [distanceUnit, setDistanceUnit] = useState(getSavedDistanceUnit);
  const [renderRichPayloads, setRenderRichPayloads] = useState(getSavedRenderRichPayloads);
  const [showPathHopWidth, setShowPathHopWidth] = useState(getSavedShowPathHopWidth);
  const previousHashRef = useRef('');
  const isOpeningSettingsRef = useRef(false);
  const pushedSettingsEntryRef = useRef(false);

  // A role with no settings (the kiosk) opened on `#settings/...`: drop the hash.
  const settingsRefused = showSettingsState && !showSettings;
  useEffect(() => {
    if (settingsRefused && parseHashSettingsSection() !== null) {
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, [settingsRefused]);

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
    if (!capabilities.canOpenSettings) return;

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
  }, [capabilities.canOpenSettings, handleCloseSettingsView, showSettings]);

  // Respond to browser back/forward navigating into or out of settings
  useEffect(() => {
    const handlePopstate = () => {
      const section = parseHashSettingsSection();
      if (section !== null) {
        rememberSettingsAnchor();
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
    distanceUnit,
    renderRichPayloads,
    showPathHopWidth,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
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
