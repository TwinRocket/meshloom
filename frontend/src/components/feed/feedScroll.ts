/** The item at the top of the viewport, with its offset in the scrollable content. */
export interface ScrollAnchor {
  key: string;
  start: number;
}

/**
 * How far `scrollTop` must move so the anchored item stays where the reader sees it after
 * rows were inserted or a page was prepended. Positive when content landed above the anchor,
 * negative when it left; 0 when the anchor itself is gone (nothing to hold on to).
 *
 * `positions` are the current items in order with their offsets in the content.
 */
export function scrollShiftForAnchor(
  anchor: ScrollAnchor | null,
  positions: readonly ScrollAnchor[]
): number {
  if (!anchor) return 0;
  const current = positions.find((item) => item.key === anchor.key);
  return current ? current.start - anchor.start : 0;
}

/** The first item whose bottom edge is below `scrollTop`: the one the reader is looking at. */
export function pickAnchor(
  items: readonly { key: string | number | bigint; start: number; end: number }[],
  scrollTop: number
): ScrollAnchor | null {
  const item = items.find((candidate) => candidate.end > scrollTop);
  return item ? { key: String(item.key), start: item.start } : null;
}
