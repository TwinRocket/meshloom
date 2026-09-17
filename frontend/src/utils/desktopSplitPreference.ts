export const DESKTOP_SPLIT_WIDTH_KEY = 'meshloom-desktop-split-width';
export const DESKTOP_SPLIT_DEFAULT = 352;
export const DESKTOP_SPLIT_MIN = 256;
export const DESKTOP_SPLIT_MAX = 480;
const CHAT_MIN = 320;
const RAIL_WIDTH = 56;
const TABLET_MAX = 1024;

export type DesktopSplitBucket = 'tablet' | 'desktop';

export function desktopSplitBucket(viewportWidth: number): DesktopSplitBucket {
  return viewportWidth < TABLET_MAX ? 'tablet' : 'desktop';
}

export function desktopSplitStorageKey(viewportWidth = 1280): string {
  return `${DESKTOP_SPLIT_WIDTH_KEY}:${desktopSplitBucket(viewportWidth)}`;
}

export function clampDesktopSplitWidth(width: number, viewportWidth = 1280): number {
  if (!Number.isFinite(width)) return DESKTOP_SPLIT_DEFAULT;
  const available = viewportWidth - RAIL_WIDTH - CHAT_MIN;
  const max = Math.max(DESKTOP_SPLIT_MIN, Math.min(DESKTOP_SPLIT_MAX, available));
  return Math.min(max, Math.max(DESKTOP_SPLIT_MIN, Math.round(width)));
}

export function getSavedDesktopSplitWidth(viewportWidth = 1280): number {
  try {
    const raw =
      localStorage.getItem(desktopSplitStorageKey(viewportWidth)) ??
      localStorage.getItem(DESKTOP_SPLIT_WIDTH_KEY);
    if (raw == null) return clampDesktopSplitWidth(DESKTOP_SPLIT_DEFAULT, viewportWidth);
    return clampDesktopSplitWidth(Number(raw), viewportWidth);
  } catch {
    return DESKTOP_SPLIT_DEFAULT;
  }
}

export function setSavedDesktopSplitWidth(width: number, viewportWidth = 1280): void {
  try {
    localStorage.setItem(
      desktopSplitStorageKey(viewportWidth),
      String(clampDesktopSplitWidth(width, viewportWidth))
    );
  } catch {
    // localStorage may be unavailable
  }
}
