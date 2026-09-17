/**
 * Desktop keyboards send on Enter; touch devices insert a newline.
 *
 * There is no "physical keyboard attached" media query. hover+fine is the
 * closest capability signal: mouse/trackpad desktops match, phones / tablets /
 * foldables typically do not. iPadOS keeps pointer:coarse even with a Magic
 * Keyboard, which is the tablet behavior we want.
 */
export const DESKTOP_ENTER_SENDS_QUERY = '(hover: hover) and (pointer: fine)';

export function getDesktopEnterSends(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(DESKTOP_ENTER_SENDS_QUERY).matches;
}

export function subscribeDesktopEnterSends(onStoreChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return () => {};
  }
  const mql = window.matchMedia(DESKTOP_ENTER_SENDS_QUERY);
  const handler = () => onStoreChange();
  mql.addEventListener('change', handler);
  return () => mql.removeEventListener('change', handler);
}
