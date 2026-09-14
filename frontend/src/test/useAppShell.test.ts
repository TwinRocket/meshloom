import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useAppShell } from '../hooks/useAppShell';
import { DESKTOP_SIDEBAR_COLLAPSED_KEY } from '../utils/sidebarRailPreference';

describe('useAppShell', () => {
  let originalHash: string;

  beforeEach(() => {
    originalHash = window.location.hash;
    localStorage.removeItem(DESKTOP_SIDEBAR_COLLAPSED_KEY);
  });

  afterEach(() => {
    window.location.hash = originalHash;
  });

  it('opens new-message modal and closes the sidebar', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleOpenNewMessage();
    });

    expect(result.current.showNewMessage).toBe(true);
    expect(result.current.sidebarOpen).toBe(false);
  });

  it('toggles settings mode and closes the sidebar', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleToggleSettingsView();
    });

    expect(result.current.showSettings).toBe(true);
    expect(result.current.sidebarOpen).toBe(false);

    act(() => {
      result.current.handleCloseSettingsView();
    });

    expect(result.current.showSettings).toBe(false);
  });

  it('initializes settings mode from the URL hash', () => {
    window.location.hash = '#settings/database';

    const { result } = renderHook(() => useAppShell());

    expect(result.current.showSettings).toBe(true);
    expect(result.current.settingsSection).toBe('database');
  });

  it('syncs the selected settings section into the URL hash', async () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.handleToggleSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/radio');
    });

    act(() => {
      result.current.setSettingsSection('fanout');
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/fanout');
    });
  });

  it('restores the previous hash when settings close', async () => {
    window.location.hash = '#channel/test/Public';

    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.handleToggleSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/radio');
    });

    act(() => {
      result.current.handleCloseSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#channel/test/Public');
    });
  });

  it('pushes a new history entry when opening settings', async () => {
    const { result } = renderHook(() => useAppShell());
    const lengthBefore = window.history.length;

    act(() => {
      result.current.handleToggleSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/radio');
    });

    expect(window.history.length).toBe(lengthBefore + 1);
  });

  it('closes settings when popstate fires with a non-settings hash', async () => {
    window.location.hash = '#settings/radio';

    const { result } = renderHook(() => useAppShell());

    expect(result.current.showSettings).toBe(true);

    act(() => {
      window.location.hash = '#channel/abc/Public';
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
    });

    await waitFor(() => {
      expect(result.current.showSettings).toBe(false);
    });
  });

  it('loads the desktop sidebar rail from localStorage', () => {
    localStorage.setItem(DESKTOP_SIDEBAR_COLLAPSED_KEY, 'true');

    const { result } = renderHook(() => useAppShell());

    expect(result.current.desktopSidebarCollapsed).toBe(true);
  });

  it('toggles and persists the desktop sidebar rail', () => {
    const { result } = renderHook(() => useAppShell());

    expect(result.current.desktopSidebarCollapsed).toBe(false);

    act(() => {
      result.current.handleToggleDesktopSidebar();
    });

    expect(result.current.desktopSidebarCollapsed).toBe(true);
    expect(localStorage.getItem(DESKTOP_SIDEBAR_COLLAPSED_KEY)).toBe('true');

    act(() => {
      result.current.handleToggleDesktopSidebar();
    });

    expect(result.current.desktopSidebarCollapsed).toBe(false);
    expect(localStorage.getItem(DESKTOP_SIDEBAR_COLLAPSED_KEY)).toBeNull();
  });

  it('toggles the cracker shell without affecting sidebar state', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleToggleCracker();
    });

    expect(result.current.showCracker).toBe(true);
    expect(result.current.sidebarOpen).toBe(true);
  });

  it('restores where you were when settings are simply closed', async () => {
    window.location.hash = '#live';
    const { result } = renderHook(() => useAppShell());

    act(() => result.current.handleToggleSettingsView());
    await waitFor(() => expect(window.location.hash).toBe('#settings/radio'));

    act(() => result.current.handleCloseSettingsView());
    await waitFor(() => expect(window.location.hash).not.toContain('settings'));
  });

  it('does not restore it when leaving settings for somewhere else', async () => {
    // The restoration is a history.back(), and its popstate lands after the new
    // destination has been set — which quietly returned the reader to the tool
    // they had left instead of the one they asked for.
    window.location.hash = '#live';
    const { result } = renderHook(() => useAppShell());

    act(() => result.current.handleToggleSettingsView());
    await waitFor(() => expect(window.location.hash).toBe('#settings/radio'));

    act(() => result.current.handleCloseSettingsView(false));

    await waitFor(() => expect(window.location.hash).not.toContain('settings'));
    expect(window.location.hash).not.toBe('#live');
  });
});
