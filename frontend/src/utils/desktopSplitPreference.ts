export const DESKTOP_SPLIT_WIDTH_KEY = 'meshloom-desktop-split-width';
export const DESKTOP_SPLIT_DEFAULT = 352;
export const DESKTOP_SPLIT_MIN = 256;
export const DESKTOP_SPLIT_MAX = 480;
const CHAT_MIN = 320;
const RAIL_WIDTH = 56;

export function clampDesktopSplitWidth(width: number, viewportWidth = 1280): number {
  if (!Number.isFinite(width)) return DESKTOP_SPLIT_DEFAULT;
  const available = viewportWidth - RAIL_WIDTH - CHAT_MIN;
  const max = Math.max(DESKTOP_SPLIT_MIN, Math.min(DESKTOP_SPLIT_MAX, available));
  return Math.min(max, Math.max(DESKTOP_SPLIT_MIN, Math.round(width)));
}

export function getSavedDesktopSplitWidth(viewportWidth = 1280): number {
  try {
    const raw = localStorage.getItem(DESKTOP_SPLIT_WIDTH_KEY);
    if (raw == null) return clampDesktopSplitWidth(DESKTOP_SPLIT_DEFAULT, viewportWidth);
    return clampDesktopSplitWidth(Number(raw), viewportWidth);
  } catch {
    return DESKTOP_SPLIT_DEFAULT;
  }
}

export function setSavedDesktopSplitWidth(width: number): void {
  try {
    localStorage.setItem(DESKTOP_SPLIT_WIDTH_KEY, String(clampDesktopSplitWidth(width)));
  } catch {
    // localStorage may be unavailable
  }
}
