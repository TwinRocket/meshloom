export const LIVE_SOUND_THEMES = ['off', 'bubbles', 'laser', 'bit8'] as const;

export type LiveSoundTheme = (typeof LIVE_SOUND_THEMES)[number];

export function isLiveSoundTheme(value: string): value is LiveSoundTheme {
  return (LIVE_SOUND_THEMES as readonly string[]).includes(value);
}
