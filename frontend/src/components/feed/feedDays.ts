import type { Message } from '../../types';

export type FeedItem =
  { kind: 'day'; key: string; at: number } | { kind: 'row'; key: string; row: Message };

/** Local calendar day of an epoch-seconds instant, `YYYY-M-D` (unpadded: only compared). */
export function localDayKey(seconds: number): string {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

/** Rows interleaved with one separator before the first row of each local day. */
export function buildFeedItems(rows: readonly Message[]): FeedItem[] {
  const items: FeedItem[] = [];
  let day: string | null = null;
  for (const row of rows) {
    const rowDay = localDayKey(row.received_at);
    if (rowDay !== day) {
      day = rowDay;
      items.push({ kind: 'day', key: `day:${rowDay}`, at: row.received_at });
    }
    items.push({ kind: 'row', key: `row:${row.id}`, row });
  }
  return items;
}

/**
 * "Today" and "Yesterday" in the UI language, a medium date for anything older. Only
 * `Intl` (no date library): the feed needs nothing finer.
 */
export function createDayLabeler(language: string): (at: number, nowSec: number) => string {
  const relative = new Intl.RelativeTimeFormat(language, { numeric: 'auto' });
  const date = new Intl.DateTimeFormat(language, { dateStyle: 'full' });
  const capitalise = (text: string) => text.charAt(0).toLocaleUpperCase(language) + text.slice(1);
  return (at, nowSec) => {
    const key = localDayKey(at);
    if (key === localDayKey(nowSec)) return capitalise(relative.format(0, 'day'));
    const yesterday = new Date(nowSec * 1000);
    yesterday.setDate(yesterday.getDate() - 1);
    if (key === localDayKey(yesterday.getTime() / 1000))
      return capitalise(relative.format(-1, 'day'));
    return date.format(at * 1000);
  };
}
