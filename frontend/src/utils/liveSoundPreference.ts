export const LIVE_SOUND_THEME_KEY = 'meshloom-live-sound-theme';

export const LIVE_SOUND_THEMES = ['off', 'bubbles', 'laser', 'bit8'] as const;

export type LiveSoundTheme = (typeof LIVE_SOUND_THEMES)[number];

export function isLiveSoundTheme(value: string): value is LiveSoundTheme {
  return (LIVE_SOUND_THEMES as readonly string[]).includes(value);
}

export function getSavedLiveSoundTheme(): LiveSoundTheme {
  try {
    const raw = localStorage.getItem(LIVE_SOUND_THEME_KEY);
    if (raw && isLiveSoundTheme(raw)) return raw;
  } catch {
    /* localStorage may be unavailable */
  }
  return 'off';
}

export function setSavedLiveSoundTheme(theme: LiveSoundTheme): void {
  try {
    localStorage.setItem(LIVE_SOUND_THEME_KEY, theme);
  } catch {
    /* localStorage may be unavailable */
  }
}
