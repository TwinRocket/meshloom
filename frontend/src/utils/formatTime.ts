import i18n from '../i18n';

const formatters = new Map<string, Intl.DateTimeFormat>();

/**
 * Hour and minute of a Unix timestamp (seconds) in the app's UI language, not the
 * browser's: a bare `toLocaleTimeString()` follows the OS locale and ignores the language
 * picked in Settings. Formatters are cached per language.
 */
export function formatClockTime(unixSeconds: number, language = i18n.language || 'en'): string {
  let formatter = formatters.get(language);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' });
    formatters.set(language, formatter);
  }
  return formatter.format(new Date(unixSeconds * 1000));
}
