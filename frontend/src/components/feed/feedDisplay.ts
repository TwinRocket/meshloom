import { useEffect } from 'react';

import { applyFontScale, getSavedFontScale } from '../../utils/fontScale';
import type { FeedFilters } from '../../utils/feedFilters';

/**
 * Display mode of the feed (`#feed?display=1`, the Display button): a wall screen or a kiosk.
 *
 * The mode is a layer over the reader's filters, never an edit of them: `displayFilters`
 * computes what the screen shows and the stored filters stay as they were, so leaving the
 * mode restores them for free. Hiding direct messages is a display choice, not an access
 * control (the WebSocket still delivers every frame to the browser); the `kiosk` role is
 * what locks the screen.
 */

/** Text size in display mode, on top of the reader's own font scale (the rem-based scale). */
export const DISPLAY_FONT_FACTOR = 1.25;

/** Attribute set on `<html>` while the mode is on: the CSS hides the shell's chrome with it. */
export const DISPLAY_ATTRIBUTE = 'data-feed-display';

/**
 * The filters the screen applies in display mode: channels always, direct messages only when
 * the screen opted in (`showDms`, which also decides the `type` sent to the server), and no
 * node pick, since the facet that shows and clears it is hidden.
 */
export function displayFilters(filters: FeedFilters, showDms: boolean): FeedFilters {
  return { ...filters, chan: true, priv: showDms, node: null };
}

/**
 * While `active`: the shell's chrome is hidden (CSS on `data-feed-display`) and the font scale
 * is raised by `DISPLAY_FONT_FACTOR`. The saved font scale (`localStorage`) is not touched, so
 * leaving restores the reader's own.
 */
export function useDisplayChrome(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const previousInline = root.style.fontSize;
    root.setAttribute(DISPLAY_ATTRIBUTE, '');
    applyFontScale(getSavedFontScale() * DISPLAY_FONT_FACTOR);
    return () => {
      root.removeAttribute(DISPLAY_ATTRIBUTE);
      root.style.fontSize = previousInline;
    };
  }, [active]);
}

/** Fullscreen when the browser offers it and allows it (a user gesture); silent otherwise. */
export function enterFullscreen(): void {
  try {
    const request = document.documentElement.requestFullscreen?.();
    request?.catch(() => {});
  } catch {
    // No Fullscreen API, or an iframe without `allowfullscreen`: the mode works without it.
  }
}

export function leaveFullscreen(): void {
  try {
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
  } catch {
    // Nothing to leave.
  }
}
