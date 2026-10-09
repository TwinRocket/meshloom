import i18n from '../i18n';

const formatters = new Map<string, Intl.NumberFormat>();

function getFormatter(language: string, options?: Intl.NumberFormatOptions): Intl.NumberFormat {
  const cacheKey = `${language}|${options ? JSON.stringify(options) : ''}`;
  let formatter = formatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.NumberFormat(language, options);
    formatters.set(cacheKey, formatter);
  }
  return formatter;
}

/**
 * Locale-aware number formatting that follows the app's UI language (the browser-local
 * preference), not the OS/browser locale a bare `toLocaleString()` would pick.
 * For i18next messages prefer passing the numeric `count` and `{{count, number}}`.
 */
export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return getFormatter(i18n.language || 'en', options).format(value);
}
